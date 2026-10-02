"""Acceptance group 3: size limits, memory bound, and size-field fraud."""
import gc
import os
import resource
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(__file__))

from builders import ZipBuilder, content_types_xml, main_doc_xml, make_docx, rels_xml

from docx_verify import Reason, verify_docx

MIB = 1024 * 1024

MAIN_OPEN = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
MAIN_CLOSE = b"</w:body></w:document>"


def _write_entry(out, central, name, data: bytes):
    """Write one small deflate entry with a signed data descriptor."""
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    payload = comp.compress(data) + comp.flush()
    crc, csize, usize = zlib.crc32(data), len(payload), len(data)
    name_b = name.encode()
    offset = out.tell()
    out.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0x0808, 8, 0, 0,
                          0, 0, 0, len(name_b), 0))
    out.write(name_b)
    out.write(payload)
    out.write(struct.pack("<IIII", 0x08074B50, crc, csize, usize))
    central.append((name_b, crc, csize, usize, offset))


def _write_big_main(out, central, expanded_size, declared_usize=None):
    """Stream-write the main document entry; never hold it all in memory."""
    name_b = b"word/document.xml"
    offset = out.tell()
    out.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0x0808, 8, 0, 0,
                          0, 0, 0, len(name_b), 0))
    out.write(name_b)
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    crc = 0
    csize = 0
    usize = 0

    def feed(data):
        nonlocal crc, csize, usize
        crc = zlib.crc32(data, crc)
        usize += len(data)
        payload = comp.compress(data)
        if payload:
            out.write(payload)
            csize += len(payload)

    feed(MAIN_OPEN)
    remaining = expanded_size - len(MAIN_OPEN) - len(MAIN_CLOSE)
    block = b"x" * MIB
    while remaining > 0:
        chunk = block[: min(len(block), remaining)]
        feed(chunk)
        remaining -= len(chunk)
    feed(MAIN_CLOSE)
    payload = comp.flush()
    out.write(payload)
    csize += len(payload)
    declared = declared_usize if declared_usize is not None else usize
    out.write(struct.pack("<IIII", 0x08074B50, crc, csize, declared))
    central.append((name_b, crc, csize, declared, offset))


def build_big_docx(path, expanded_size, declared_usize=None):
    with open(path, "wb") as out:
        central = []
        _write_entry(out, central, "[Content_Types].xml", content_types_xml())
        _write_entry(out, central, "_rels/.rels", rels_xml())
        _write_big_main(out, central, expanded_size, declared_usize)
        cd_offset = out.tell()
        cd_size = 0
        for name_b, crc, csize, usize, offset in central:
            rec = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0x0808,
                              8, 0, 0, crc, csize, usize, len(name_b), 0, 0,
                              0, 0, 0, offset) + name_b
            out.write(rec)
            cd_size += len(rec)
        out.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(central),
                              len(central), cd_size, cd_offset, 0))
    return str(path)


def test_near_limit_docx_passes_within_memory_bound(tmp_path):
    path = build_big_docx(tmp_path / "big.docx", 63 * MIB)
    assert os.path.getsize(path) < 10 * MIB
    gc.collect()
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result = verify_docx(path)
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert result.ok, result.reason
    assert after - before < 24 * MIB, f"peak memory grew by {after - before} bytes"


def test_real_expansion_over_limit_rejected(tmp_path):
    path = build_big_docx(tmp_path / "huge.docx", 65 * MIB)
    result = verify_docx(path)
    assert not result.ok
    assert result.reason == Reason.EXPANDED_TOO_LARGE


def test_declared_size_fraud_over_limit_rejected(tmp_path):
    # declares a tiny uncompressed size but actually expands past the limit
    path = build_big_docx(tmp_path / "fraud.docx", 65 * MIB, declared_usize=100)
    result = verify_docx(path)
    assert not result.ok
    assert result.reason in (Reason.EXPANDED_TOO_LARGE, Reason.SIZE_MISMATCH)


def test_file_over_10mib_rejected(tmp_path):
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml", main_doc_xml())
    builder.add("word/blob.bin", b"\x00" * (10 * MIB + 1), method=0)
    path = tmp_path / "toobig.docx"
    path.write_bytes(builder.build())
    result = verify_docx(str(path))
    assert not result.ok
    assert result.reason == Reason.TOO_LARGE


def test_too_many_entries_rejected():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml", main_doc_xml())
    for i in range(1023):
        builder.add(f"f{i}.txt", b"x", method=0)
    result = verify_docx(builder.build())
    assert not result.ok
    assert result.reason == Reason.TOO_MANY_ENTRIES
