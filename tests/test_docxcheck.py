import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docxcheck import verify_docx
from zipbuild import build_zip, entry

NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MAIN_TYPE = ("application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document.main+xml")
RELS_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
OFFICE_REL = ("http://schemas.openxmlformats.org/officeDocument/2006/"
              "relationships/officeDocument")


def content_types(part="/word/document.xml", ctype=MAIN_TYPE, prefix=None):
    p = "ct:" if prefix else ""
    ns = f'xmlns:{p[:-1]}=' if prefix else "xmlns="
    return (
        f'<{p}Types {ns}"{NS_CT}">'
        f'<{p}Default Extension="rels" ContentType="{RELS_TYPE}"/>'
        f'<{p}Default Extension="xml" ContentType="application/xml"/>'
        f'<{p}Override PartName="{part}" ContentType="{ctype}"/>'
        f'</{p}Types>'
    ).encode()


def rels(target="word/document.xml", count=1, mode=None, prefix=None):
    p = "r:" if prefix else ""
    ns = f'xmlns:{p[:-1]}=' if prefix else "xmlns="
    body = ""
    for i in range(count):
        m = f' TargetMode="{mode}"' if mode else ""
        body += (f'<{p}Relationship Id="rId{i+1}" Type="{OFFICE_REL}" '
                 f'Target="{target}"{m}/>')
    return f'<{p}Relationships {ns}"{NS_REL}">{body}</{p}Relationships>'.encode()


def document(prefix="w"):
    return (
        f'<{prefix}:document xmlns:{prefix}="{NS_W}">'
        f'<{prefix}:body/></{prefix}:document>'
    ).encode()


def make_docx(main_path="word/document.xml", main_data=None, descriptor=None,
              comment=b"", prefix=None, doc_prefix="w", extra_entries=None,
              ct_part=None, rels_target=None, method=8):
    entries = [
        entry("[Content_Types].xml",
              content_types(part=ct_part or "/" + main_path, prefix=prefix),
              method=method),
        entry("_rels/.rels",
              rels(target=rels_target or main_path, prefix=prefix),
              method=method),
        entry(main_path, main_data if main_data is not None
              else document(doc_prefix), method=method, descriptor=descriptor),
    ]
    entries.extend(extra_entries or [])
    return build_zip(entries, comment=comment)


class Base(unittest.TestCase):
    def check(self, blob: bytes):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.write(blob)
            path = fh.name
        try:
            return verify_docx(path)
        finally:
            os.unlink(path)

    def assertAccepted(self, blob):
        v = self.check(blob)
        self.assertTrue(v.accepted, f"unexpectedly rejected: {v.reason}")

    def assertRejected(self, blob, needle=""):
        v = self.check(blob)
        self.assertFalse(v.accepted, "unexpectedly accepted")
        if needle:
            self.assertIn(needle, v.reason)


class TestValidDocuments(Base):
    def test_basic_deflate(self):
        self.assertAccepted(make_docx())

    def test_stored_method(self):
        self.assertAccepted(make_docx(method=0))

    def test_custom_main_path(self):
        self.assertAccepted(make_docx(main_path="custom/dir/main.xml"))

    def test_main_path_with_leading_slash_target(self):
        self.assertAccepted(make_docx(rels_target="/word/document.xml"))

    def test_default_namespace_document(self):
        doc = f'<document xmlns="{NS_W}"><body/></document>'.encode()
        self.assertAccepted(make_docx(main_data=doc))

    def test_unusual_prefixes_everywhere(self):
        self.assertAccepted(make_docx(prefix="x", doc_prefix="qq"))

    def test_descriptor_with_signature(self):
        self.assertAccepted(make_docx(descriptor="sig"))

    def test_descriptor_without_signature(self):
        self.assertAccepted(make_docx(descriptor="raw"))

    def test_comment_with_fake_eocd_signature(self):
        comment = b"hello PK\x05\x06 world \x00\x00 fake end record"
        self.assertAccepted(make_docx(comment=comment))

    def test_directory_entries(self):
        extra = [entry("word/", b"", method=0)]
        self.assertAccepted(make_docx(extra_entries=extra))

    def test_utf8_entry_name(self):
        extra = [entry("word/注释.txt", b"hi", utf8=True)]
        self.assertAccepted(make_docx(extra_entries=extra))

    def test_extension_default_type(self):
        ct = (
            f'<Types xmlns="{NS_CT}">'
            f'<Default Extension="rels" ContentType="{RELS_TYPE}"/>'
            f'<Default Extension="xml" ContentType="{MAIN_TYPE}"/>'
            f'</Types>'
        ).encode()
        blob = build_zip([
            entry("[Content_Types].xml", ct),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", document()),
        ])
        self.assertAccepted(blob)


if __name__ == "__main__":
    unittest.main()
