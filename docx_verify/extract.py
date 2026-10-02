"""Streaming decompression with CRC and size enforcement."""
from __future__ import annotations

import io
import zlib
from typing import Iterator

from .errors import Reason
from .zipmodel import Entry
from .zipreader import Reject

MAX_EXPANDED_TOTAL = 64 * 1024 * 1024
CHUNK = 64 * 1024


class Expander:
    """Expands entries one at a time, enforcing a global output budget."""

    def __init__(self, fp: io.BufferedIOBase, limit: int = MAX_EXPANDED_TOTAL) -> None:
        self._fp = fp
        self._limit = limit
        self.total = 0

    def expand(self, entry: Entry, metered: bool = True) -> Iterator[bytes]:
        """Yield decompressed chunks, validating size and CRC on completion."""
        crc = 0
        produced = 0
        if entry.method == 0:
            gen = self._stored_chunks(entry)
        else:
            gen = self._deflate_chunks(entry)
        for chunk in gen:
            crc = zlib.crc32(chunk, crc)
            produced += len(chunk)
            if metered:
                self.total += len(chunk)
                if self.total > self._limit:
                    raise Reject(Reason.EXPANDED_TOO_LARGE)
            yield chunk
        if produced != entry.uncompressed_size:
            raise Reject(Reason.SIZE_MISMATCH)
        if crc != entry.crc:
            raise Reject(Reason.CRC_MISMATCH)

    def _read_chunks(self, entry: Entry) -> Iterator[bytes]:
        remaining = entry.compressed_size
        self._fp.seek(entry.data_offset)
        while remaining > 0:
            data = self._fp.read(min(CHUNK, remaining))
            if not data:
                raise Reject(Reason.TRUNCATED)
            remaining -= len(data)
            yield data

    def _stored_chunks(self, entry: Entry) -> Iterator[bytes]:
        if entry.compressed_size != entry.uncompressed_size:
            raise Reject(Reason.SIZE_MISMATCH)
        yield from self._read_chunks(entry)

    def _deflate_chunks(self, entry: Entry) -> Iterator[bytes]:
        decomp = zlib.decompressobj(-15)
        for data in self._read(entry):
            out = decomp.decompress(data, CHUNK)
            while decomp.unconsumed_tail:
                if out:
                    yield out
                out = decomp.decompress(decomp.unconsumed_tail, CHUNK)
            if out:
                yield out
        tail = decomp.flush()
        if tail:
            yield tail
        if not decomp.eof:
            raise Reject(Reason.TRUNCATED)
        if decomp.unused_data:
            raise Reject(Reason.SIZE_MISMATCH)

    def _read(self, entry: Entry) -> Iterator[bytes]:
        return self._read_chunks(entry)
