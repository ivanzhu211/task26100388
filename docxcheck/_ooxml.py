"""包级 OOXML 核验与公开 API。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from docxcheck._xml import ContentTypes, Relationships, XmlReject, make_sink
from docxcheck._zip import ZipReject, inflate_entry, parse_archive

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_ENTRIES = 1024
MAX_EXPANDED = 64 * 1024 * 1024

MAIN_DOC_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
CONTENT_TYPES_NAME = "[Content_Types].xml"
RELS_NAME = "_rels/.rels"


@dataclass
class Verdict:
    """核验结论：accepted 为 False 时 reason 给出拒绝原因。"""

    accepted: bool
    reason: str = ""


def _resolve_target(target: str) -> str:
    """把包级关系目标规范化为包内路径；非法时抛 ZipReject。"""
    if "%" in target:
        raise ZipReject("percent-encoded relationship target not allowed")
    for ch in ("\\", "\x00", ":"):
        if ch in target:
            raise ZipReject("forbidden character in relationship target")
    path = target[1:] if target.startswith("/") else target
    segments = path.split("/")
    if not segments or any(seg in ("", ".", "..") for seg in segments):
        raise ZipReject("relationship target is not a canonical relative path")
    return "/".join(segments)


def _main_doc_content_type(ct: ContentTypes, target: str) -> str:
    """类型覆盖优先于扩展名默认值。"""
    override = ct.overrides.get("/" + target)
    if override is not None:
        return override
    ext = target.rsplit(".", 1)[-1] if "." in target else ""
    return ct.defaults.get(ext, "")


def verify_docx(path: str) -> Verdict:
    """核验 path 指向的文件是否为可入库的 DOCX，不修改原文件。"""
    try:
        if os.path.getsize(path) > MAX_FILE_SIZE:
            return Verdict(False, "file exceeds 10 MiB limit")
        with open(path, "rb") as fh:
            data = fh.read()

        entries, budget = parse_archive(data, MAX_EXPANDED)
        if len(entries) > MAX_ENTRIES:
            return Verdict(False, "too many archive entries")
        by_name = {e.name: e for e in entries}

        ct_entry = by_name.get(CONTENT_TYPES_NAME)
        rels_entry = by_name.get(RELS_NAME)
        if ct_entry is None or ct_entry.is_dir:
            return Verdict(False, "missing [Content_Types].xml")
        if rels_entry is None or rels_entry.is_dir:
            return Verdict(False, "missing _rels/.rels")

        holder: dict = {}
        ct_sink = make_sink("content_types", holder)
        inflate_entry(data, ct_entry, budget, ct_sink)
        ct_sink.close()
        rels_sink = make_sink("rels", holder)
        inflate_entry(data, rels_entry, budget, rels_sink)
        rels_sink.close()

        content_types: ContentTypes = holder["content_types"]
        rels: Relationships = holder["rels"]

        targets = rels.office_document_targets
        if len(targets) != 1:
            return Verdict(False, "expected exactly one officeDocument relationship")
        target, internal = targets[0]
        if not internal:
            return Verdict(False, "officeDocument relationship must be internal")
        main_name = _resolve_target(target)
        main_entry = by_name.get(main_name)
        if main_entry is None or main_entry.is_dir:
            return Verdict(False, "officeDocument target missing in package")

        if _main_doc_content_type(content_types, main_name) != MAIN_DOC_TYPE:
            return Verdict(False, "main document content type mismatch")

        doc_sink = make_sink("document", holder)
        for entry in entries:
            if entry in (ct_entry, rels_entry):
                continue
            sink = doc_sink if entry is main_entry else None
            inflate_entry(data, entry, budget, sink)
        doc_sink.close()
        return Verdict(True)
    except (ZipReject, XmlReject) as exc:
        return Verdict(False, str(exc))
    except OSError as exc:
        return Verdict(False, f"io error: {exc}")
