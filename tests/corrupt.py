"""Byte-level patch helpers to build corrupted archives."""
from __future__ import annotations

import struct

LOCAL_CSIZE = 18
LOCAL_USIZE = 22
LOCAL_CRC = 14
LOCAL_FLAGS = 6
LOCAL_METHOD = 8
CD_CRC = 16
CD_CSIZE = 20
CD_USIZE = 24
CD_FLAGS = 8
CD_METHOD = 10
CD_LHO = 42


def set_u16(data: bytes, offset: int, value: int) -> bytes:
    buf = bytearray(data)
    struct.pack_into("<H", buf, offset, value)
    return bytes(buf)


def set_u32(data: bytes, offset: int, value: int) -> bytes:
    buf = bytearray(data)
    struct.pack_into("<I", buf, offset, value)
    return bytes(buf)


def eocd_offset(data: bytes) -> int:
    idx = data.rfind(b"PK\x05\x06")
    assert idx >= 0
    return idx


def cd_records(data: bytes) -> list[dict]:
    """Return central directory record offsets and local header offsets."""
    eocd = eocd_offset(data)
    count, cd_size, cd_offset = struct.unpack_from("<HII", data, eocd + 10)
    records = []
    pos = cd_offset
    for _ in range(count):
        assert data[pos : pos + 4] == b"PK\x01\x02"
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, pos + 28)
        local = struct.unpack_from("<I", data, pos + CD_LHO)[0]
        records.append({"cd": pos, "local": local})
        pos += 46 + name_len + extra_len + comment_len
    return records


def patch_both(data: bytes, rec: dict, local_field: int, cd_field: int, value: int, size: int = 4) -> bytes:
    setter = set_u16 if size == 2 else set_u32
    data = setter(data, rec["local"] + local_field, value)
    return setter(data, rec["cd"] + cd_field, value)


def add_sfx_prefix(data: bytes, prefix_len: int = 100) -> bytes:
    """Prepend a self-extracting stub and fix up recorded offsets."""
    records = cd_records(data)
    out = data
    for rec in records:
        out = set_u32(out, rec["cd"] + CD_LHO, rec["local"] + prefix_len)
    eocd = eocd_offset(out)
    cd_offset = struct.unpack_from("<I", out, eocd + 16)[0]
    out = set_u32(out, eocd + 16, cd_offset + prefix_len)
    return b"\x00" * prefix_len + out
