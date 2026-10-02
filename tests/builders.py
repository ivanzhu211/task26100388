"""Helpers to hand-craft ZIP/DOCX byte streams for tests."""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

WML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MAIN_CT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)


def _deflate(data: bytes) -> bytes:
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    return comp.compress(data) + comp.flush()


@dataclass
class _Item:
    name: str
    data: bytes
    method: int = 8
    descriptor: str | None = None  # None | "sig" | "nosig"


@dataclass
class ZipBuilder:
    items: list[_Item] = field(default_factory=list)

    def add(self, name: str, data: bytes, method: int = 8, descriptor: str | None = None):
        self.items.append(_Item(name, data, method, descriptor))
        return self

    def build(self, comment: bytes = b"", cd_order: list[int] | None = None) -> bytes:
        out = bytearray()
        central = []
        for item in self.items:
            name = item.name.encode("utf-8")
            flags = 0x0800 | (0x08 if item.descriptor else 0)
            if item.method == 8:
                payload = _deflate(item.data)
            else:
                payload = item.data
            crc = zlib.crc32(item.data)
            csize, usize = len(payload), len(item.data)
            offset = len(out)
            if item.descriptor:
                lcrc = lcsize = lusize = 0
            else:
                lcrc, lcsize, lusize = crc, csize, usize
            out += struct.pack(
                "<IHHHHHIIIHH", 0x04034B50, 20, flags, item.method, 0, 0,
                lcrc, lcsize, lusize, len(name), 0,
            )
            out += name
            out += payload
            if item.descriptor == "sig":
                out += struct.pack("<IIII", 0x08074B50, crc, csize, usize)
            elif item.descriptor == "nosig":
                out += struct.pack("<III", crc, csize, usize)
            central.append(
                struct.pack(
                    "<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, flags, item.method,
                    0, 0, crc, csize, usize, len(name), 0, 0, 0, 0, 0, offset,
                )
                + name
            )
        cd_offset = len(out)
        order = cd_order if cd_order is not None else range(len(central))
        cd = b"".join(central[i] for i in order)
        out += cd
        out += struct.pack(
            "<IHHHHIIH", 0x06054B50, 0, 0, len(central), len(central),
            len(cd), cd_offset, len(comment),
        )
        out += comment
        return bytes(out)


def content_types_xml(main_part: str = "/word/document.xml") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="{main_part}" ContentType="{MAIN_CT}"/>'
        "</Types>"
    ).encode()


def rels_xml(target: str = "word/document.xml") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        f'Target="{target}"/>'
        "</Relationships>"
    ).encode()


def main_doc_xml(prefix: str | None = "w", body: bytes = b"") -> bytes:
    if prefix:
        return (
            f'<{prefix}:document xmlns:{prefix}="{WML_NS}"><{prefix}:body>'.encode()
            + body
            + f"</{prefix}:body></{prefix}:document>".encode()
        )
    return (
        f'<document xmlns="{WML_NS}"><body>'.encode()
        + body
        + b"</body></document>"
    )


def make_docx(
    main_path: str = "word/document.xml",
    prefix: str | None = "w",
    descriptor: str | None = None,
    comment: bytes = b"",
    method: int = 8,
    cd_order: list[int] | None = None,
    target: str | None = None,
    main_data: bytes | None = None,
) -> bytes:
    builder = ZipBuilder()
    builder.add("[Content_Types].xml", content_types_xml("/" + main_path), method, descriptor)
    builder.add("_rels/.rels", rels_xml(target or main_path), method, descriptor)
    builder.add(main_path, main_data if main_data is not None else main_doc_xml(prefix), method, descriptor)
    return builder.build(comment=comment, cd_order=cd_order)
