import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_docxcheck import (Base, NS_CT, NS_REL, NS_W, MAIN_TYPE, RELS_TYPE,
                            OFFICE_REL, content_types, rels, document,
                            make_docx)
from zipbuild import build_zip, entry


class TestRejectContainer(Base):
    def test_local_central_crc_mismatch(self):
        blob = make_docx()
        bad = entry("word/document.xml", document(), local={"crc": 1})
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            bad,
        ])
        self.assertRejected(blob, "mismatch")

    def test_central_declares_wrong_usize(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document(),
                  central={"usize": 10 ** 9}),
        ])
        self.assertRejected(blob)

    def test_descriptor_inconsistent_with_central(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document(), descriptor="sig",
                  dd=(0, 0, 0)),
        ])
        self.assertRejected(blob, "descriptor")

    def test_overlapping_entries(self):
        # 第一条目声明的压缩大小超出实际数据，区间覆盖后续条目头
        big = len(content_types()) + 200
        blob = build_zip([
            entry("[Content_Types].xml", content_types(),
                  local={"csize": big}, central={"csize": big}),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "overlap")

    def test_truncated_deflate_stream(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document(), truncate=5),
        ])
        self.assertRejected(blob, "truncated")

    def test_crc_mismatch_actual(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document(),
                  central={"crc": 123}, local={"crc": 123}),
        ])
        self.assertRejected(blob, "crc")

    def test_trailing_garbage_after_eocd(self):
        self.assertRejected(make_docx() + b"junk")

    def test_self_extracting_prefix(self):
        blob = make_docx()
        # 重建带前缀的归档（中央目录偏移相应后移）
        from zipbuild import entry as mk
        blob = build_zip([
            mk("[Content_Types].xml", content_types()),
            mk("_rels/.rels", rels()),
            mk("word/document.xml", document()),
        ], prefix=b"MZ fake sfx")
        self.assertRejected(blob, "prefix")

    def test_zip64_locator_rejected(self):
        self.assertRejected(make_docx_zip64())

    def test_zip64_sentinel_counts(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document()),
        ], eocd={"count": 0xFFFF})
        self.assertRejected(blob, "ZIP64")

    def test_encrypted_entry(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document(), flags_extra=0x1),
        ])
        self.assertRejected(blob, "encrypt")

    def test_multi_disk(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document()),
        ], eocd={"disk": 1})
        self.assertRejected(blob, "multi-disk")

    def test_duplicate_names(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document()),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "duplicate")

    def test_bad_names(self):
        for bad in ("/abs.xml", "a\\b.xml", "a//b.xml", "a/./b.xml",
                    "a/../b.xml"):
            blob = build_zip([
                entry("[Content_Types].xml", content_types()),
                entry("_rels/.rels", rels()),
                entry("word/document.xml", document()),
                entry(bad, b"x"),
            ])
            self.assertRejected(blob)

    def test_non_ascii_name_without_utf8_flag(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document()),
            entry("café.txt".encode("utf-8"), b"x"),  # 非 ASCII 且未置 UTF-8 标志
        ])
        self.assertRejected(blob)


def make_docx_zip64():
    return build_zip([
        entry("[Content_Types].xml", content_types()),
        entry("_rels/.rels", rels()),
        entry("word/document.xml", document()),
    ], zip64_locator=True)


class TestRejectPackage(Base):
    def test_missing_content_types(self):
        blob = build_zip([
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "Content_Types")

    def test_missing_rels(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "rels")

    def test_relationship_target_missing(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels(target="word/elsewhere.xml")),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "missing")

    def test_relationship_points_to_non_main_part(self):
        ct = (
            f'<Types xmlns="{NS_CT}">'
            f'<Default Extension="rels" ContentType="{RELS_TYPE}"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/word/document.xml" ContentType="{MAIN_TYPE}"/>'
            f'</Types>'
        ).encode()
        blob = build_zip([
            entry("[Content_Types].xml", ct),
            entry("_rels/.rels", rels(target="word/other.xml")),
            entry("word/document.xml", document()),
            entry("word/other.xml", document()),
        ])
        self.assertRejected(blob, "content type")

    def test_zero_office_document_rels(self):
        r = f'<Relationships xmlns="{NS_REL}"></Relationships>'.encode()
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", r),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "exactly one")

    def test_two_office_document_rels(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels(count=2)),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "exactly one")

    def test_external_relationship(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels(mode="External")),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "internal")

    def test_percent_encoded_target(self):
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels(target="word/document%2Exml")),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "percent")

    def test_macro_content_type(self):
        macro = ("application/vnd.ms-word.document."
                 "macroEnabled.main+xml")
        blob = build_zip([
            entry("[Content_Types].xml", content_types(ctype=macro)),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "content type")

    def test_duplicate_override(self):
        ct = (
            f'<Types xmlns="{NS_CT}">'
            f'<Default Extension="rels" ContentType="{RELS_TYPE}"/>'
            f'<Override PartName="/word/document.xml" ContentType="{MAIN_TYPE}"/>'
            f'<Override PartName="/word/document.xml" ContentType="{MAIN_TYPE}"/>'
            f'</Types>'
        ).encode()
        blob = build_zip([
            entry("[Content_Types].xml", ct),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "duplicate")

    def test_duplicate_default(self):
        ct = (
            f'<Types xmlns="{NS_CT}">'
            f'<Default Extension="xml" ContentType="a"/>'
            f'<Default Extension="xml" ContentType="b"/>'
            f'<Override PartName="/word/document.xml" ContentType="{MAIN_TYPE}"/>'
            f'</Types>'
        ).encode()
        blob = build_zip([
            entry("[Content_Types].xml", ct),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "duplicate")

    def test_dtd_in_document(self):
        doc = (b'<!DOCTYPE document [<!ENTITY x "y">]>'
               + document())
        self.assertRejected(make_docx(main_data=doc), "DTD")

    def test_dtd_in_rels(self):
        r = (b'<!DOCTYPE Relationships [<!ENTITY x "y">]>' + rels())
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", r),
            entry("word/document.xml", document()),
        ])
        self.assertRejected(blob, "DTD")

    def test_malformed_xml(self):
        self.assertRejected(make_docx(main_data=b"<w:document"), "xml")

    def test_wrong_root_namespace(self):
        doc = b'<document xmlns="http://example.com/other"/>'
        self.assertRejected(make_docx(main_data=doc), "root")

    def test_strict_ooxml_namespace_rejected(self):
        strict_w = ("http://purl.oclc.org/ooxml/wordprocessingml/main")
        doc = f'<document xmlns="{strict_w}"/>'.encode()
        self.assertRejected(make_docx(main_data=doc), "root")


if __name__ == "__main__":
    unittest.main()
