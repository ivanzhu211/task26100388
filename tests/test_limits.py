import os
import sys
import tempfile
import tracemalloc
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docxcheck import verify_docx
from test_docxcheck import Base, content_types, rels
from zipbuild import build_zip, entry

MIB = 1024 * 1024
MEMORY_LIMIT = 24 * MIB


def big_document(size: int) -> bytes:
    head = ('<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>').encode()
    tail = b"</w:t></w:r></w:p></w:body></w:document>"
    return head + b"A" * (size - len(head) - len(tail)) + tail


def make_big_docx(doc_size: int) -> bytes:
    return build_zip([
        entry("[Content_Types].xml", content_types()),
        entry("_rels/.rels", rels()),
        entry("word/document.xml", big_document(doc_size)),
    ])


class TestLimits(Base):
    def test_near_64mib_expansion_accepted_with_memory_budget(self):
        blob = make_big_docx(63 * MIB)
        self.assertLessEqual(len(blob), 10 * MIB, "压缩后不应超过 10 MiB")
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.write(blob)
            path = fh.name
        try:
            tracemalloc.start()
            try:
                verdict = verify_docx(path)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        finally:
            os.unlink(path)
        self.assertTrue(verdict.accepted,
                        f"unexpectedly rejected: {verdict.reason}")
        self.assertLessEqual(peak, MEMORY_LIMIT,
                             f"peak memory {peak} exceeds 24 MiB")

    def test_real_expansion_over_limit_rejected(self):
        blob = make_big_docx(64 * MIB)
        self.assertRejected(blob, "expanded size limit")

    def test_forged_declared_usize_rejected(self):
        doc = big_document(1 * MIB)
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", doc,
                  local={"usize": 100}, central={"usize": 100}),
        ])
        self.assertRejected(blob, "size mismatch")

    def test_forged_small_usize_cannot_smuggle_expansion(self):
        # 声明展开很小、实际展开超过 64 MiB：按真实数据拒绝
        doc = big_document(64 * MIB)
        blob = build_zip([
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", doc,
                  local={"usize": 100}, central={"usize": 100}),
        ])
        self.assertRejected(blob, "expanded size limit")

    def test_file_over_10mib_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.truncate(10 * MIB + 1)
            path = fh.name
        try:
            verdict = verify_docx(path)
        finally:
            os.unlink(path)
        self.assertFalse(verdict.accepted)
        self.assertIn("10 MiB", verdict.reason)

    def test_too_many_entries_rejected(self):
        entries = [
            entry("[Content_Types].xml", content_types()),
            entry("_rels/.rels", rels()),
            entry("word/document.xml", big_document(200)),
        ]
        entries += [entry(f"n/{i}.txt", b"x") for i in range(1022)]
        self.assertRejected(build_zip(entries), "too many")


if __name__ == "__main__":
    unittest.main()
