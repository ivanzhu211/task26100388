"""OOXML package (OPC) level checks on top of a parsed archive."""
from __future__ import annotations

import re
from typing import Iterable, Optional
from xml.parsers import expat

from .errors import Reason
from .extract import Expander
from .xmlutil import check_xml
from .zipmodel import Archive, Entry
from .zipreader import Reject

CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_DOC_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
WML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _find_entry(archive: Archive, name: str) -> Optional[Entry]:
    for entry in archive.entries:
        if entry.name == name:
            return entry
    return None


def _stream_parser() -> expat.xmlparser:
    parser = expat.ParserCreate(namespace_separator="}")

    def on_doctype(*args: object) -> None:
        raise Reject(Reason.DTD_FORBIDDEN)

    parser.StartDoctypeDeclHandler = on_doctype
    parser.EntityDeclHandler = on_doctype
    return parser


def _feed(parser: expat.xmlparser, chunks: Iterable[bytes]) -> None:
    try:
        for chunk in chunks:
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except Reject:
        raise
    except expat.ExpatError:
        raise Reject(Reason.BAD_XML)


def _parse_content_types(chunks: Iterable[bytes]) -> tuple[dict, dict]:
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    depth = [0]
    root_ok = [False]
    parser = _stream_parser()

    def on_start(name: str, attrs: dict) -> None:
        depth[0] += 1
        if depth[0] == 1:
            if name != CT_NS + "}Types":
                raise Reject(Reason.BAD_XML)
            root_ok[0] = True
            return
        if depth[0] != 2 or not name.startswith(CT_NS + "}"):
            return
        local = name[len(CT_NS) + 1 :]
        if local == "Default":
            ext = attrs.get("Extension")
            ctype = attrs.get("ContentType")
            if not ext or not ctype:
                raise Reject(Reason.BAD_XML)
            key = ext.lower()
            if key in defaults:
                raise Reject(Reason.DUPLICATE_DEFAULT)
            defaults[key] = ctype
        elif local == "Override":
            part = attrs.get("PartName")
            ctype = attrs.get("ContentType")
            if not part or not ctype:
                raise Reject(Reason.BAD_XML)
            if part in overrides:
                raise Reject(Reason.DUPLICATE_OVERRIDE)
            overrides[part] = ctype

    def on_end(name: str) -> None:
        depth[0] -= 1

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    _feed(parser, chunks)
    if not root_ok[0]:
        raise Reject(Reason.BAD_XML)
    return defaults, overrides


def _parse_rels(chunks: Iterable[bytes]) -> list[tuple[str, str, Optional[str]]]:
    rels: list[tuple[str, str, Optional[str]]] = []
    depth = [0]
    root_ok = [False]
    parser = _stream_parser()

    def on_start(name: str, attrs: dict) -> None:
        depth[0] += 1
        if depth[0] == 1:
            if name != RELS_NS + "}Relationships":
                raise Reject(Reason.BAD_XML)
            root_ok[0] = True
            return
        if depth[0] == 2 and name == RELS_NS + "}Relationship":
            rel_type = attrs.get("Type")
            target = attrs.get("Target")
            if rel_type is None or target is None:
                raise Reject(Reason.BAD_XML)
            rels.append((rel_type, target, attrs.get("TargetMode")))

    def on_end(name: str) -> None:
        depth[0] -= 1

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    _feed(parser, chunks)
    if not root_ok[0]:
        raise Reject(Reason.BAD_XML)
    return rels


def _resolve_target(target: str) -> str:
    if "%" in target or "#" in target or "\\" in target or "\x00" in target:
        raise Reject(Reason.BAD_TARGET)
    if _SCHEME_RE.match(target):
        raise Reject(Reason.BAD_TARGET)
    path = target[1:] if target.startswith("/") else target
    segments = path.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        raise Reject(Reason.BAD_TARGET)
    return "/".join(segments)


def _content_type_for(name: str, defaults: dict, overrides: dict) -> Optional[str]:
    override = overrides.get("/" + name)
    if override is not None:
        return override
    base = name.rsplit("/", 1)[-1]
    if "." in base:
        ext = base.rsplit(".", 1)[-1].lower()
        return defaults.get(ext)
    return None


def check_package(fp: object, archive: Archive, expander: Expander) -> None:
    """Validate content types, relationships and the main document part."""
    ct_entry = _find_entry(archive, "[Content_Types].xml")
    if ct_entry is None:
        raise Reject(Reason.MISSING_CONTENT_TYPES)
    rels_entry = _find_entry(archive, "_rels/.rels")
    if rels_entry is None:
        raise Reject(Reason.MISSING_RELS)

    defaults, overrides = _parse_content_types(expander.expand(ct_entry, metered=False))
    rels = _parse_rels(expander.expand(rels_entry, metered=False))

    office = [r for r in rels if r[0] == OFFICE_DOC_REL]
    if len(office) != 1:
        raise Reject(Reason.BAD_OFFICE_REL)
    _rel_type, target, mode = office[0]
    if mode not in (None, "Internal"):
        raise Reject(Reason.BAD_OFFICE_REL)

    main_name = _resolve_target(target)
    main_entry = _find_entry(archive, main_name)
    if main_entry is None or main_entry.is_dir:
        raise Reject(Reason.TARGET_MISSING)

    if _content_type_for(main_name, defaults, overrides) != MAIN_CONTENT_TYPE:
        raise Reject(Reason.WRONG_CONTENT_TYPE)

    ns, local = check_xml(expander.expand(main_entry, metered=False))
    if ns != WML_NS or local != "document":
        raise Reject(Reason.WRONG_ROOT)
