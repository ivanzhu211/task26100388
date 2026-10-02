"""Strict ZIP container parsing: EOCD, central directory, local headers."""
from __future__ import annotations

import io
import struct

from .errors import Reason
from .zipmodel import Archive, Entry

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_ENTRIES = 1024

EOCD_SIG = b"PK\x05\x06"
CD_SIG = b"PK\x01\x02"
LOCAL_SIG = b"PK\x03\x04"
DD_SIG = b"PK\x07\x08"

EOCD_MIN = 22
CD_FIXED = 46
LOCAL_FIXED = 30
MAX_COMMENT = 65535

ALLOWED_FLAGS = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 11)
ENCRYPT_FLAGS = (1 << 0) | (1 << 6) | (1 << 13)


class Reject(Exception):
    """Internal control-flow exception carrying a reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _read_at(fp: io.BufferedIOBase, offset: int, length: int, file_size: int) -> bytes:
    if offset < 0 or length < 0 or offset + length > file_size:
        raise Reject(Reason.REGION_OUT_OF_BOUNDS)
    fp.seek(offset)
    data = fp.read(length)
    if len(data) != length:
        raise Reject(Reason.TRUNCATED)
    return data


def _find_eocd_candidates(fp: io.BufferedIOBase, file_size: int) -> list[int]:
    """Return absolute offsets of structurally consistent EOCD records.

    A candidate is consistent when its comment ends exactly at end of file,
    so a fake signature inside a real comment is skipped unless it happens
    to be self-consistent (handled by trying each candidate in turn).
    """
    window = min(file_size, EOCD_MIN + MAX_COMMENT)
    fp.seek(file_size - window)
    tail = fp.read(window)
    base = file_size - window
    candidates: list[int] = []
    pos = 0
    while True:
        idx = tail.find(EOCD_SIG, pos)
        if idx < 0:
            break
        pos = idx + 1
        if idx + EOCD_MIN > len(tail):
            continue
        comment_len = struct.unpack_from("<H", tail, idx + 20)[0]
        if base + idx + EOCD_MIN + comment_len == file_size:
            candidates.append(base + idx)
    candidates.sort(reverse=True)
    return candidates


def _decode_name(raw: bytes, flags: int) -> str:
    if flags & (1 << 11):
        try:
            name = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Reject(Reason.BAD_NAME)
    else:
        try:
            name = raw.decode("ascii")
        except UnicodeDecodeError:
            raise Reject(Reason.BAD_NAME)
    _validate_name(name)
    return name


def _validate_name(name: str) -> None:
    if not name or "\x00" in name or "\\" in name:
        raise Reject(Reason.BAD_NAME)
    if name.startswith("/"):
        raise Reject(Reason.BAD_NAME)
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        raise Reject(Reason.BAD_NAME)
    segments = name.split("/")
    body = segments[:-1] if name.endswith("/") else segments
    for seg in body:
        if seg in ("", ".", ".."):
            raise Reject(Reason.BAD_NAME)


def _check_flags(flags: int) -> None:
    if flags & ENCRYPT_FLAGS:
        raise Reject(Reason.ENCRYPTED)
    if flags & ~(ALLOWED_FLAGS | ENCRYPT_FLAGS):
        raise Reject(Reason.UNSUPPORTED_FLAG)


def _parse_central_directory(
    fp: io.BufferedIOBase, file_size: int, eocd: bytes, eocd_off: int
) -> Archive:
    (
        disk_no,
        cd_disk,
        entries_disk,
        entries_total,
        cd_size,
        cd_offset,
        _comment_len,
    ) = struct.unpack_from("<HHHHIIH", eocd, 4)
    if disk_no != 0 or cd_disk != 0 or entries_disk != entries_total:
        raise Reject(Reason.MULTI_DISK)
    if (
        entries_total == 0xFFFF
        or cd_size == 0xFFFFFFFF
        or cd_offset == 0xFFFFFFFF
    ):
        raise Reject(Reason.ZIP64)
    if entries_total > MAX_ENTRIES:
        raise Reject(Reason.TOO_MANY_ENTRIES)
    if cd_offset + cd_size > eocd_off:
        raise Reject(Reason.REGION_OUT_OF_BOUNDS)

    archive = Archive(
        cd_start=cd_offset,
        cd_end=cd_offset + cd_size,
        eocd_start=eocd_off,
        eocd_end=file_size,
    )
    pos = cd_offset
    for _ in range(entries_total):
        if pos + CD_FIXED > archive.cd_end:
            raise Reject(Reason.TRUNCATED)
        header = _read_at(fp, pos, CD_FIXED, file_size)
        if header[0:4] != CD_SIG:
            raise Reject(Reason.METADATA_MISMATCH)
        (
            _made,
            _needed,
            flags,
            method,
            _mtime,
            _mdate,
            crc,
            csize,
            usize,
            name_len,
            extra_len,
            comment_len,
            disk_start,
            _iattr,
            _eattr,
            local_offset,
        ) = struct.unpack_from("<HHHHHHIIIHHHHHII", header, 4)
        if disk_start != 0:
            raise Reject(Reason.MULTI_DISK)
        if crc == 0xFFFFFFFF or csize == 0xFFFFFFFF or usize == 0xFFFFFFFF:
            raise Reject(Reason.ZIP64)
        if local_offset == 0xFFFFFFFF:
            raise Reject(Reason.ZIP64)
        _check_flags(flags)
        if method not in (0, 8):
            raise Reject(Reason.UNSUPPORTED_METHOD)
        rec_len = CD_FIXED + name_len + extra_len + comment_len
        if pos + rec_len > archive.cd_end:
            raise Reject(Reason.TRUNCATED)
        raw_name = _read_at(fp, pos + CD_FIXED, name_len, file_size)
        name = _decode_name(raw_name, flags)
        archive.entries.append(
            Entry(
                name=name,
                flags=flags,
                method=method,
                crc=crc,
                compressed_size=csize,
                uncompressed_size=usize,
                local_offset=local_offset,
            )
        )
        pos += rec_len
    if pos != archive.cd_end:
        raise Reject(Reason.METADATA_MISMATCH)
    return archive


def _parse_local_headers(
    fp: io.BufferedIOBase, file_size: int, archive: Archive
) -> None:
    for entry in archive.entries:
        header = _read_at(fp, entry.local_offset, LOCAL_FIXED, file_size)
        if header[0:4] != LOCAL_SIG:
            raise Reject(Reason.METADATA_MISMATCH)
        (
            _needed,
            flags,
            method,
            _mtime,
            _mdate,
            crc,
            csize,
            usize,
            name_len,
            extra_len,
        ) = struct.unpack_from("<HHHHHIIIHH", header, 4)
        if flags != entry.flags or method != entry.method:
            raise Reject(Reason.METADATA_MISMATCH)
        raw_name = _read_at(fp, entry.local_offset + LOCAL_FIXED, name_len, file_size)
        if _decode_name(raw_name, flags) != entry.name:
            raise Reject(Reason.METADATA_MISMATCH)
        if entry.has_descriptor:
            for local_val, cd_val in ((crc, entry.crc), (csize, entry.compressed_size), (usize, entry.uncompressed_size)):
                if local_val not in (0, cd_val):
                    raise Reject(Reason.METADATA_MISMATCH)
        else:
            if (
                crc != entry.crc
                or csize != entry.compressed_size
                or usize != entry.uncompressed_size
            ):
                raise Reject(Reason.METADATA_MISMATCH)
        entry.data_offset = entry.local_offset + LOCAL_FIXED + name_len + extra_len
        entry.descriptor_offset = entry.data_offset + entry.compressed_size
        entry.end_offset = entry.descriptor_offset
        if entry.has_descriptor:
            _parse_descriptor(fp, file_size, entry)


def _parse_descriptor(fp: io.BufferedIOBase, file_size: int, entry: Entry) -> None:
    head = _read_at(fp, entry.descriptor_offset, 4, file_size)
    if head == DD_SIG:
        body = _read_at(fp, entry.descriptor_offset + 4, 12, file_size)
        entry.end_offset = entry.descriptor_offset + 16
    else:
        body = head + _read_at(fp, entry.descriptor_offset + 4, 8, file_size)
        entry.end_offset = entry.descriptor_offset + 12
    crc, csize, usize = struct.unpack("<III", body)
    if (
        crc != entry.crc
        or csize != entry.compressed_size
        or usize != entry.uncompressed_size
    ):
        raise Reject(Reason.METADATA_MISMATCH)


def _check_regions(archive: Archive, file_size: int) -> None:
    regions: list[tuple[int, int]] = []
    for entry in archive.entries:
        regions.append((entry.local_offset, entry.end_offset))
    regions.append((archive.cd_start, archive.cd_end))
    regions.append((archive.eocd_start, archive.eocd_end))
    for start, end in regions:
        if start < 0 or end > file_size or start > end:
            raise Reject(Reason.REGION_OUT_OF_BOUNDS)
    regions.sort()
    for (prev_start, prev_end), (cur_start, cur_end) in zip(regions, regions[1:]):
        if cur_start < prev_end:
            raise Reject(Reason.REGION_OVERLAP)
    if archive.entries:
        first = min(e.local_offset for e in archive.entries)
        if first != 0:
            raise Reject(Reason.PREFIX_DATA)


def _check_duplicates(archive: Archive) -> None:
    seen: set[str] = set()
    for entry in archive.entries:
        if entry.name in seen:
            raise Reject(Reason.DUPLICATE_NAME)
        seen.add(entry.name)


def parse_archive(fp: io.BufferedIOBase, file_size: int) -> Archive:
    """Parse and structurally validate the ZIP container."""
    if file_size < EOCD_MIN:
        raise Reject(Reason.NOT_ZIP)
    candidates = _find_eocd_candidates(fp, file_size)
    if not candidates:
        raise Reject(Reason.EOCD_NOT_FOUND)
    first_error: Reject | None = None
    for eocd_off in candidates:
        try:
            eocd = _read_at(fp, eocd_off, EOCD_MIN, file_size)
            archive = _parse_central_directory(fp, file_size, eocd, eocd_off)
            _parse_local_headers(fp, file_size, archive)
            _check_regions(archive, file_size)
            _check_duplicates(archive)
            return archive
        except Reject as exc:
            if first_error is None:
                first_error = exc
    assert first_error is not None
    raise first_error
