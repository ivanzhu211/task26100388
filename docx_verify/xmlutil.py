"""Streaming XML well-formedness checks with DTD/entity rejection."""
from __future__ import annotations

from typing import Iterable, Optional, Tuple
from xml.parsers import expat

from .errors import Reason
from .zipreader import Reject


def check_xml(chunks: Iterable[bytes]) -> Tuple[Optional[str], Optional[str]]:
    """Validate well-formedness; return (root namespace, root local name).

    Raises Reject on malformed XML, DTD, or entity declarations.
    """
    root: list[Tuple[Optional[str], Optional[str]]] = []
    parser = expat.ParserCreate(namespace_separator="}")

    def on_start(name: str, attrs: dict) -> None:
        if not root:
            if "}" in name:
                ns, local = name.split("}", 1)
            else:
                ns, local = None, name
            root.append((ns, local))

    def on_doctype(*args: object) -> None:
        raise Reject(Reason.DTD_FORBIDDEN)

    parser.StartElementHandler = on_start
    parser.StartDoctypeDeclHandler = on_doctype
    parser.EntityDeclHandler = on_doctype
    try:
        for chunk in chunks:
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except Reject:
        raise
    except expat.ExpatError:
        raise Reject(Reason.BAD_XML)
    if not root:
        raise Reject(Reason.BAD_XML)
    return root[0]
