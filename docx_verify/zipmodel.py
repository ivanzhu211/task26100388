"""Data model for a strictly parsed ZIP archive."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entry:
    """One archive entry merged from central directory and local header."""

    name: str
    flags: int
    method: int
    crc: int
    compressed_size: int
    uncompressed_size: int
    local_offset: int
    data_offset: int = 0
    descriptor_offset: int = 0
    end_offset: int = 0

    @property
    def is_dir(self) -> bool:
        return self.name.endswith("/")

    @property
    def has_descriptor(self) -> bool:
        return bool(self.flags & 0x08)


@dataclass
class Archive:
    """Parsed archive: entries plus the byte regions they occupy."""

    entries: list[Entry] = field(default_factory=list)
    cd_start: int = 0
    cd_end: int = 0
    eocd_start: int = 0
    eocd_end: int = 0
