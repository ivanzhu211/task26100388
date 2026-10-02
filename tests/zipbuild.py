"""测试用 ZIP 字节构造器：完全控制头字段、描述符形式、注释与偏移。"""

from __future__ import annotations

import struct
import zlib

SIG_LFH = 0x04034B50
SIG_DD = 0x08074B50
SIG_CDH = 0x02014B50
SIG_EOCD = 0x06054B50
SIG_ZIP64_LOC = 0x07064B50


def deflate(data: bytes, level: int = 9) -> bytes:
    obj = zlib.compressobj(level, zlib.DEFLATED, -15)
    return obj.compress(data) + obj.flush()


def entry(
    name,
    data: bytes = b"",
    method: int = 8,
    descriptor=None,  # None | "sig" | "raw"
    utf8: bool = False,
    flags_extra: int = 0,
    local: dict | None = None,     # 覆盖局部头 crc/csize/usize
    central: dict | None = None,   # 覆盖中央目录 crc/csize/usize
    dd: tuple | None = None,       # 覆盖描述符 (crc, csize, usize)
    truncate: int | None = None,   # 把压缩数据截断到 N 字节
    force_offset: int | None = None,
) -> dict:
    if isinstance(name, bytes):
        raw_name = name
    else:
        raw_name = name.encode("utf-8") if utf8 else name.encode("ascii")
    payload = data if method == 0 else deflate(data)
    if truncate is not None:
        payload = payload[:truncate]
    crc = zlib.crc32(data)
    flags = flags_extra | (0x800 if utf8 else 0) | (0x8 if descriptor else 0)
    real = {"crc": crc, "csize": len(payload), "usize": len(data)}
    cent = dict(real)
    cent.update(central or {})
    if descriptor:
        loc = {"crc": 0, "csize": 0, "usize": 0}
    else:
        loc = dict(real)
    loc.update(local or {})
    return {
        "raw_name": raw_name,
        "payload": payload,
        "method": method,
        "flags": flags,
        "local": loc,
        "central": cent,
        "descriptor": descriptor,
        "dd": dd or (cent["crc"], cent["csize"], cent["usize"]),
        "force_offset": force_offset,
    }


def build_zip(
    entries: list,
    comment: bytes = b"",
    prefix: bytes = b"",
    suffix: bytes = b"",
    eocd: dict | None = None,
    zip64_locator: bool = False,
) -> bytes:
    writes: list[tuple[int, bytes]] = []
    central_records = []
    cursor = len(prefix)
    if prefix:
        writes.append((0, prefix))

    for e in entries:
        off = e["force_offset"] if e["force_offset"] is not None else cursor
        loc = e["local"]
        lfh = struct.pack(
            "<IHHHHHIIIHH",
            SIG_LFH, 20, e["flags"], e["method"], 0, 0,
            loc["crc"], loc["csize"], loc["usize"],
            len(e["raw_name"]), 0,
        ) + e["raw_name"]
        blob = lfh + e["payload"]
        if e["descriptor"]:
            crc, csize, usize = e["dd"]
            dd = struct.pack("<III", crc, csize, usize)
            if e["descriptor"] == "sig":
                dd = struct.pack("<I", SIG_DD) + dd
            blob += dd
        writes.append((off, blob))
        central_records.append((off, e))
        cursor = max(cursor, off + len(blob))

    cd_offset = cursor
    cd = b""
    for off, e in central_records:
        cent = e["central"]
        cd += struct.pack(
            "<IHHHHHHIIIHHHHHII",
            SIG_CDH, 20, 20, e["flags"], e["method"], 0, 0,
            cent["crc"], cent["csize"], cent["usize"],
            len(e["raw_name"]), 0, 0, 0, 0, 0, off,
        ) + e["raw_name"]
    writes.append((cd_offset, cd))
    cursor += len(cd)

    if zip64_locator:
        loc = struct.pack("<IIQI", SIG_ZIP64_LOC, 0, cursor + 20, 1)
        writes.append((cursor, loc))
        cursor += len(loc)

    ev = {"disk": 0, "cd_disk": 0, "count": len(entries), "cd_size": len(cd),
          "cd_offset": cd_offset}
    ev.update(eocd or {})
    eocd_rec = struct.pack(
        "<IHHHHIIH",
        SIG_EOCD, ev["disk"], ev["cd_disk"], ev["count"], ev["count"],
        ev["cd_size"], ev["cd_offset"], len(comment),
    ) + comment
    writes.append((cursor, eocd_rec))
    cursor += len(eocd_rec)

    buf = bytearray(cursor)
    for off, blob in writes:
        buf[off : off + len(blob)] = blob
    return bytes(buf) + suffix
