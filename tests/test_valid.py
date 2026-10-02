"""Acceptance group 1: legitimate documents must pass."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest
from builders import ZipBuilder, content_types_xml, main_doc_xml, make_docx, rels_xml

from docx_verify import verify_docx


def test_default_deflate():
    assert verify_docx(make_docx()).ok


def test_stored_method():
    assert verify_docx(make_docx(method=0)).ok


@pytest.mark.parametrize("descriptor", ["sig", "nosig"])
def test_data_descriptors(descriptor):
    assert verify_docx(make_docx(descriptor=descriptor)).ok


def test_custom_main_path():
    assert verify_docx(make_docx(main_path="doc/main.xml")).ok


def test_deep_main_path():
    assert verify_docx(make_docx(main_path="a/b/c/document.xml")).ok


def test_leading_slash_target():
    assert verify_docx(make_docx(target="/word/document.xml")).ok


@pytest.mark.parametrize("prefix", ["w", "foo", "x9"])
def test_custom_xml_prefix(prefix):
    assert verify_docx(make_docx(prefix=prefix)).ok


def test_default_namespace_no_prefix():
    assert verify_docx(make_docx(prefix=None)).ok


def test_comment_with_fake_eocd_signature():
    comment = b"see notes PK\x05\x06 and more PK\x05\x06 trailing"
    assert verify_docx(make_docx(comment=comment)).ok


def test_plain_comment():
    assert verify_docx(make_docx(comment=b"archive comment")).ok


def test_shuffled_central_directory_order():
    assert verify_docx(make_docx(cd_order=[2, 0, 1])).ok


def test_extra_entries_and_directory_entry():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/", b"", method=0)
    builder.add("word/document.xml", main_doc_xml())
    builder.add("word/styles.xml", main_doc_xml().replace(b"document", b"styles"))
    assert verify_docx(builder.build()).ok


def test_utf8_filename_entry():
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml())
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml", main_doc_xml())
    builder.add("word/注释.txt", "héllo".encode())
    assert verify_docx(builder.build()).ok


def test_verify_from_path(tmp_path):
    path = tmp_path / "sample.docx"
    path.write_bytes(make_docx())
    assert verify_docx(str(path)).ok


def test_default_extension_content_type_no_override():
    from builders import MAIN_CT, WML_NS

    types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        f'<Default Extension="xml" ContentType="{MAIN_CT}"/>'
        "</Types>"
    ).encode()
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", types)
    builder.add("_rels/.rels", rels_xml())
    builder.add("word/document.xml", main_doc_xml())
    assert verify_docx(builder.build()).ok


def test_main_doc_at_package_root():
    assert verify_docx(make_docx(main_path="document.xml")).ok
