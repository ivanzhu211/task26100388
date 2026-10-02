"""Acceptance group 2: contradictory, corrupt or misdirected files must be rejected."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(__file__))

import corrupt
from builders import ZipBuilder, content_types_xml, main_doc_xml, make_docx, rels_xml

from docx_verify import Reason, verify_docx


def check(data, reason):
    result = verify_docx(data)
    assert not result.ok, "expected rejection"
    assert result.reason == reason, f"expected {reason}, got {result.reason}"


# --- container level ---

def test_not_a_zip():
    check(b"PK" + b"\x00" * 100, Reason.EOCD_NOT_FOUND)


def test_plain_text():
    check(b"hello world", Reason.NOT_ZIP)


def test_truncated_file():
    data = make_docx()
    check(data[: len(data) - 30], Reason.EOCD_NOT_FOUND)


def test_trailing_garbage_after_eocd():
    check(make_docx() + b"garbage", Reason.EOCD_NOT_FOUND)


def test_sfx_prefix():
    check(corrupt.add_sfx_prefix(make_docx()), Reason.PREFIX_DATA)


def test_encrypted_flag():
    data = make_docx()
    rec = corrupt.cd_records(data)[2]
    flags = struct.unpack_from("<H", data, rec["local"] + 6)[0]
    check(corrupt.patch_both(data, rec, 6, 8, flags | 1, size=2), Reason.ENCRYPTED)


def test_unsupported_method():
    data = make_docx()
    rec = corrupt.cd_records(data)[2]
    check(corrupt.patch_both(data, rec, 8, 10, 9, size=2), Reason.UNSUPPORTED_METHOD)


def test_zip64_sentinel():
    data = make_docx()
    eocd = corrupt.eocd_offset(data)
    data = corrupt.set_u16(data, eocd + 8, 0xFFFF)
    check(corrupt.set_u16(data, eocd + 10, 0xFFFF), Reason.ZIP64)


def test_multi_disk():
    data = make_docx()
    eocd = corrupt.eocd_offset(data)
    check(corrupt.set_u16(data, eocd + 4, 1), Reason.MULTI_DISK)


# --- metadata consistency ---

def test_crc_mismatch_local_vs_cd():
    data = make_docx()
    rec = corrupt.cd_records(data)[2]
    check(corrupt.set_u32(data, rec["local"] + 14, 0xDEADBEEF), Reason.METADATA_MISMATCH)


def test_method_mismatch_local_vs_cd():
    data = make_docx()
    rec = corrupt.cd_records(data)[2]
    check(corrupt.set_u16(data, rec["local"] + 8, 0), Reason.METADATA_MISMATCH)


def test_descriptor_mismatch():
    data = make_docx(descriptor="sig")
    rec = corrupt.cd_records(data)[2]
    # corrupt the data descriptor's CRC (after local header + name + payload)
    csize = struct.unpack_from("<I", data, rec["cd"] + 20)[0]
    nlen = struct.unpack_from("<H", data, rec["local"] + 26)[0]
    dd = rec["local"] + 30 + nlen + csize
    assert data[dd : dd + 4] == b"PK\x07\x08"
    check(corrupt.set_u32(data, dd + 4, 0xDEADBEEF), Reason.METADATA_MISMATCH)


def test_region_overlap():
    data = make_docx()
    recs = corrupt.cd_records(data)
    # point the third entry's local header into the middle of the first entry
    victim = recs[0]["local"] + 5
    check(corrupt.set_u32(data, recs[2]["cd"] + corrupt.CD_LHO, victim), Reason.METADATA_MISMATCH)


def test_region_overlap_real_headers():
    # a.txt declares a compressed size that swallows b.txt's local header
    import struct as st
    import zlib as zl

    payload_a = b"A" * 100
    payload_b = b"B" * 100
    fake_csize = 150  # real payload is only 100 bytes

    def local(name, payload, csize):
        return (
            st.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0x0800, 0, 0, 0,
                    zl.crc32(payload), csize, len(payload), len(name), 0)
            + name
            + payload
        )

    la = local(b"a.txt", payload_a, fake_csize)
    lb = local(b"b.txt", payload_b, len(payload_b))
    body = la + lb
    cd_offset = len(body)
    cd = b""
    cd += st.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0x0800, 0, 0, 0,
                  zl.crc32(payload_a), fake_csize, len(payload_a), 5, 0, 0, 0, 0, 0, 0) + b"a.txt"
    cd += st.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0x0800, 0, 0, 0,
                  zl.crc32(payload_b), len(payload_b), len(payload_b), 5, 0, 0, 0, 0, 0,
                  len(la)) + b"b.txt"
    eocd = st.pack("<IHHHHIIH", 0x06054B50, 0, 0, 2, 2, len(cd), cd_offset, 0)
    check(body + cd + eocd, Reason.REGION_OVERLAP)


def test_truncated_deflate_stream():
    data = make_docx()
    rec = corrupt.cd_records(data)[2]
    csize = struct.unpack_from("<I", data, rec["cd"] + 20)[0]
    # shrink declared compressed size by 2 so the deflate stream is cut short
    data = corrupt.patch_both(data, rec, 18, 20, csize - 2)
    check(data, Reason.TRUNCATED)


def test_declared_size_fraud():
    data = make_docx()
    rec = corrupt.cd_records(data)[2]
    usize = struct.unpack_from("<I", data, rec["cd"] + 24)[0]
    check(corrupt.patch_both(data, rec, 22, 24, usize - 1), Reason.SIZE_MISMATCH)


def test_crc_fraud():
    data = make_docx()
    rec = corrupt.cd_records(data)[2]
    crc = struct.unpack_from("<I", data, rec["cd"] + 16)[0]
    check(corrupt.patch_both(data, rec, 14, 16, crc ^ 0xFF), Reason.CRC_MISMATCH)


# --- entry names ---

def test_duplicate_names():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml", main_doc_xml())
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.DUPLICATE_NAME)


def test_absolute_path_name():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    builder.add("/word/document.xml", main_doc_xml())
    check(builder.build(), Reason.BAD_NAME)


def test_backslash_name():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    builder.add("word\\document.xml", main_doc_xml())
    check(builder.build(), Reason.BAD_NAME)


def test_dotdot_name():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    builder.add("../document.xml", main_doc_xml())
    check(builder.build(), Reason.BAD_NAME)


def test_non_ascii_without_utf8_flag():
    data = make_docx()
    rec = corrupt.cd_records(data)[2]
    flags = struct.unpack_from("<H", data, rec["local"] + 6)[0]
    data = corrupt.patch_both(data, rec, 6, 8, flags & ~0x0800, size=2)
    # inject a non-ascii byte into the stored name (same length)
    idx = data.find(b"document.xml")
    buf = bytearray(data)
    buf[idx] = 0xE9
    check(bytes(buf), Reason.BAD_NAME)


# --- package level ---

def test_missing_content_types():
    builder = ZipBuilder()
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.MISSING_CONTENT_TYPES)


def test_missing_rels():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.MISSING_RELS)


def test_rel_target_missing():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml("word/other.xml"))
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.TARGET_MISSING)


def test_rel_target_dotdot():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml("../document.xml"))
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.BAD_TARGET)


def test_rel_target_percent_encoded():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml("word/document%2Exml"))
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.BAD_TARGET)


def test_rel_external():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml("word/document.xml").replace(
        b'Target="word/document.xml"',
        b'Target="word/document.xml" TargetMode="External"'))
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.BAD_OFFICE_REL)


def test_two_office_rels():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    double = rels_xml().replace(
        b"</Relationships>",
        b'<Relationship Id="rId2" '
        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        b'Target="word/document.xml"/></Relationships>')
    builder.add("_rels/.rels", double)
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.BAD_OFFICE_REL)


def test_no_office_rel():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml().replace(b"officeDocument", b"styles"))
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.BAD_OFFICE_REL)


def test_macro_content_type():
    builder = ZipBuilder()
    macro_ct = content_types_xml().replace(
        b"wordprocessingml.document.main+xml",
        b"ms-word.document.macroEnabled.main+xml")
    builder.add("[Content_Types].xml", macro_ct)
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.WRONG_CONTENT_TYPE)


def test_duplicate_default():
    builder = ZipBuilder()
    dup = content_types_xml().replace(
        b'<Default Extension="xml"',
        b'<Default Extension="xml" ContentType="text/xml"/>'
        b'<Default Extension="xml"')
    builder.add("[Content_Types].xml", dup)
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.DUPLICATE_DEFAULT)


def test_duplicate_override():
    builder = ZipBuilder()
    dup = content_types_xml().replace(
        b"</Types>",
        b'<Override PartName="/word/document.xml" ContentType="text/xml"/></Types>')
    builder.add("[Content_Types].xml", dup)
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml", main_doc_xml())
    check(builder.build(), Reason.DUPLICATE_OVERRIDE)


def test_dtd_in_main_doc():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    evil = (b'<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY x "y">]>'
            + main_doc_xml())
    builder.add("word/document.xml", evil)
    check(builder.build(), Reason.DTD_FORBIDDEN)


def test_malformed_main_doc():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml", b"<w:document><unclosed>")
    check(builder.build(), Reason.BAD_XML)


def test_wrong_root_element():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml",
                b'<w:documentX xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    check(builder.build(), Reason.WRONG_ROOT)


def test_strict_namespace_root_rejected():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    strict = (b'<w:document xmlns:w="http://purl.oclc.org/ooxml/wordprocessingml/main"/>')
    builder.add("word/document.xml", strict)
    check(builder.build(), Reason.WRONG_ROOT)
