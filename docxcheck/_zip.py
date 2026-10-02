"""严格的 ZIP 容器解析（不修改原文件，只读）。

只支持 Stored(0) 与 Deflate(8)；拒绝 ZIP64、分卷、加密、自解压前缀、
结束记录及其注释之后的额外数据。所有结构区间不得越界或重叠。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

SIG_LFH = 0x04034B50
SIG_DD = 0x08074B50
SIG_CDH = 0x02014B50
SIG_EOCD = 0x06054B50
SIG_ZIP64_EOCD = 0x06064B50
SIG_ZIP64_LOC = 0x07064B50

METHOD_STORED = 0
METHOD_DEFLATE = 8

FLAG_ENCRYPTED = 0x0001
FLAG_DESCRIPTOR = 0x0008
FLAG_UTF8 = 0x0800

EXTRA_ZIP64 = 0x0001

MAX_COMMENT = 65535
EOCD_MIN = 22
CDH_MIN = 46
LFH_MIN = 30
CHUNK = 65536


@dataclass
class ZipEntry:
    """单个归档条目的解析结果（offset 均为绝对区间）。"""

    name: str
    method: int
    flags: int
    declared_size: int
    declared_csize: int
    declared_crc: int
    data_offset: int
    is_dir: bool


class ZipReject(Exception):
    """ZIP 容器层面拒绝。"""


class _Budget:
    """全部条目实际展开总字节预算。"""

    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def spend(self, n: int) -> None:
        if n > self.remaining:
            raise ZipReject("expanded size limit exceeded")
        self.remaining -= n


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _find_eocd(data: bytes) -> int:
    """定位唯一满足“注释恰好结束于文件尾”的 EOCD，返回其偏移。"""
    if len(data) < EOCD_MIN:
        raise ZipReject("file too small for end of central directory")
    start = max(0, len(data) - EOCD_MIN - MAX_COMMENT)
    matches = []
    pos = len(data) - EOCD_MIN
    while pos >= start:
        if _u32(data, pos) == SIG_EOCD and pos + EOCD_MIN + _u16(data, pos + 20) == len(data):
            matches.append(pos)
        pos -= 1
    if not matches:
        raise ZipReject("no end of central directory or trailing data after it")
    if len(matches) > 1:
        raise ZipReject("ambiguous end of central directory")
    return matches[0]


def _check_extra_no_zip64(extra: bytes) -> None:
    off = 0
    while off + 4 <= len(extra):
        tag, size = struct.unpack_from("<HH", extra, off)
        if tag == EXTRA_ZIP64:
            raise ZipReject("ZIP64 extra field not allowed")
        off += 4 + size
    if off != len(extra):
        raise ZipReject("malformed extra field")


def _decode_name(raw: bytes, flags: int) -> str:
    try:
        if flags & FLAG_UTF8:
            name = raw.decode("utf-8", "strict")
        else:
            name = raw.decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise ZipReject("entry name not decodable") from exc
    if "\x00" in name or "\\" in name:
        raise ZipReject("entry name contains forbidden character")
    if name.startswith("/"):
        raise ZipReject("entry name is an absolute path")
    segments = name.split("/")
    if name.endswith("/"):
        segments = segments[:-1]
    for seg in segments:
        if seg in ("", ".", ".."):
            raise ZipReject("entry name has empty or dot path segment")
    return name


def _parse_central(data: bytes, cd_offset: int, count: int) -> list:
    entries = []
    off = cd_offset
    for _ in range(count):
        if off + CDH_MIN > len(data) or _u32(data, off) != SIG_CDH:
            raise ZipReject("malformed central directory")
        flags = _u16(data, off + 8)
        method = _u16(data, off + 10)
        crc = _u32(data, off + 16)
        csize = _u32(data, off + 20)
        usize = _u32(data, off + 24)
        nlen = _u16(data, off + 28)
        elen = _u16(data, off + 30)
        clen = _u16(data, off + 32)
        lfh_off = _u32(data, off + 42)
        if csize == 0xFFFFFFFF or usize == 0xFFFFFFFF or lfh_off == 0xFFFFFFFF:
            raise ZipReject("ZIP64 not allowed")
        end = off + CDH_MIN + nlen + elen + clen
        if end > len(data):
            raise ZipReject("central directory entry out of bounds")
        raw_name = data[off + CDH_MIN : off + CDH_MIN + nlen]
        _check_extra_no_zip64(data[off + CDH_MIN + nlen : off + CDH_MIN + nlen + elen])
        entries.append((raw_name, flags, method, crc, csize, usize, lfh_off, off, end))
        off = end
    return entries, off


def _parse_descriptor(data: bytes, off: int, crc: int, csize: int, usize: int) -> int:
    """解析数据描述符（可带签名），返回描述符长度。"""
    if off + 16 <= len(data) and _u32(data, off) == SIG_DD:
        if (
            _u32(data, off + 4) == crc
            and _u32(data, off + 8) == csize
            and _u32(data, off + 12) == usize
        ):
            return 16
    if off + 12 <= len(data):
        if (
            _u32(data, off) == crc
            and _u32(data, off + 4) == csize
            and _u32(data, off + 8) == usize
        ):
            return 12
    raise ZipReject("data descriptor missing or inconsistent")


def parse_archive(data: bytes, expand_limit: int) -> tuple[list[ZipEntry], _Budget]:
    """解析并校验整个 ZIP 容器，返回 (条目列表, 展开预算)。"""
    eocd = _find_eocd(data)
    if eocd >= 4 and _u32(data, eocd - 4) == SIG_ZIP64_LOC:
        raise ZipReject("ZIP64 not allowed")
    if eocd >= 20 and _u32(data, eocd - 20) == SIG_ZIP64_LOC:
        raise ZipReject("ZIP64 not allowed")
    disk = _u16(data, eocd + 4)
    cd_disk = _u16(data, eocd + 6)
    count_disk = _u16(data, eocd + 8)
    count = _u16(data, eocd + 10)
    cd_size = _u32(data, eocd + 12)
    cd_offset = _u32(data, eocd + 16)
    if disk != 0 or cd_disk != 0 or count_disk != count:
        raise ZipReject("multi-disk archive not allowed")
    if count == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        raise ZipReject("ZIP64 not allowed")
    if cd_offset + cd_size > eocd:
        raise ZipReject("central directory out of bounds")

    raw_entries, cd_end = _parse_central(data, cd_offset, count)
    if cd_end != cd_offset + cd_size:
        raise ZipReject("central directory size mismatch")

    intervals = [(cd_offset, cd_end, "central directory"), (eocd, len(data), "eocd")]
    entries: list[ZipEntry] = []
    seen: set[str] = set()
    budget = _Budget(expand_limit)

    for raw_name, flags, method, crc, csize, usize, lfh_off, _cd_off, _cd_end in raw_entries:
        if flags & FLAG_ENCRYPTED:
            raise ZipReject("encrypted entries not allowed")
        if method not in (METHOD_STORED, METHOD_DEFLATE):
            raise ZipReject("unsupported compression method")
        name = _decode_name(raw_name, flags)
        if name in seen:
            raise ZipReject("duplicate entry name")
        seen.add(name)

        if lfh_off + LFH_MIN > len(data) or _u32(data, lfh_off) != SIG_LFH:
            raise ZipReject("local file header missing")
        l_flags = _u16(data, lfh_off + 6)
        l_method = _u16(data, lfh_off + 8)
        l_crc = _u32(data, lfh_off + 14)
        l_csize = _u32(data, lfh_off + 18)
        l_usize = _u32(data, lfh_off + 22)
        l_nlen = _u16(data, lfh_off + 26)
        l_elen = _u16(data, lfh_off + 28)
        head_end = lfh_off + LFH_MIN + l_nlen + l_elen
        if head_end > len(data):
            raise ZipReject("local file header out of bounds")
        if l_flags != flags or l_method != method:
            raise ZipReject("local/central header mismatch")
        if data[lfh_off + LFH_MIN : lfh_off + LFH_MIN + l_nlen] != raw_name:
            raise ZipReject("local/central name mismatch")
        _check_extra_no_zip64(data[lfh_off + LFH_MIN + l_nlen : head_end])
        if flags & FLAG_DESCRIPTOR:
            if l_crc != 0 or l_csize != 0 or l_usize != 0:
                raise ZipReject("local header not zeroed with data descriptor")
        else:
            if (l_crc, l_csize, l_usize) != (crc, csize, usize):
                raise ZipReject("local/central size or crc mismatch")

        data_start = head_end
        data_end = data_start + csize
        if data_end > len(data):
            raise ZipReject("compressed data out of bounds")
        intervals.append((lfh_off, data_end, "entry"))
        if flags & FLAG_DESCRIPTOR:
            dd_len = _parse_descriptor(data, data_end, crc, csize, usize)
            intervals.append((data_end, data_end + dd_len, "descriptor"))

        entries.append(
            ZipEntry(
                name=name,
                method=method,
                flags=flags,
                declared_size=usize,
                declared_csize=csize,
                declared_crc=crc,
                data_offset=data_start,
                is_dir=name.endswith("/"),
            )
        )

    if entries:
        first = min(iv[0] for iv in intervals if iv[2] == "entry")
        if first != 0:
            raise ZipReject("self-extracting prefix not allowed")
    intervals.sort()
    for (prev_s, prev_e, _), (cur_s, cur_e, _) in zip(intervals, intervals[1:]):
        if cur_s < prev_e:
            raise ZipReject("overlapping archive structures")
    return entries, budget


def inflate_entry(data: bytes, entry: ZipEntry, budget: _Budget, sink=None) -> None:
    """流式解压单个条目，校验实际长度与 CRC，输出块送入 sink。"""
    raw = memoryview(data)[entry.data_offset : entry.data_offset + entry.declared_csize]
    crc = 0
    total = 0

    def emit(chunk: bytes) -> None:
        nonlocal crc, total
        budget.spend(len(chunk))
        crc = zlib.crc32(chunk, crc)
        total += len(chunk)
        if sink is not None:
            sink(chunk)

    if entry.method == METHOD_STORED:
        for off in range(0, len(raw), CHUNK):
            emit(bytes(raw[off : off + CHUNK]))
    else:
        dec = zlib.decompressobj(-15)
        for off in range(0, len(raw), CHUNK):
            out = dec.decompress(bytes(raw[off : off + CHUNK]), CHUNK)
            if out:
                emit(out)
            while dec.unconsumed_tail:
                out = dec.decompress(dec.unconsumed_tail, CHUNK)
                if out:
                    emit(out)
        out = dec.flush()
        if out:
            emit(out)
        if not dec.eof:
            raise ZipReject("truncated deflate stream")
        if dec.unused_data:
            raise ZipReject("garbage after deflate stream")

    if total != entry.declared_size:
        raise ZipReject("expanded size mismatch")
    if crc != entry.declared_crc:
        raise ZipReject("crc mismatch")
