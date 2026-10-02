"""Top-level verification orchestration."""
from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Optional, Union

from .errors import Reason
from .extract import Expander
from .package import check_package
from .zipreader import MAX_FILE_SIZE, Reject, parse_archive


@dataclass
class VerifyResult:
    """Outcome of verifying one candidate DOCX file."""

    ok: bool
    reason: Optional[str] = None


def _verify_stream(fp: io.BufferedIOBase, file_size: int) -> VerifyResult:
    try:
        archive = parse_archive(fp, file_size)
        expander = Expander(fp)
        for entry in archive.entries:
            for _chunk in expander.expand(entry):
                pass
        check_package(fp, archive, expander)
    except Reject as exc:
        return VerifyResult(ok=False, reason=exc.reason)
    return VerifyResult(ok=True)


def verify_docx(source: Union[str, os.PathLike, bytes]) -> VerifyResult:
    """Verify that *source* (path or bytes) is an acceptable DOCX package.

    Never modifies the input; only reports acceptability and a reason code.
    """
    if isinstance(source, (str, os.PathLike)):
        try:
            file_size = os.path.getsize(source)
        except OSError:
            return VerifyResult(ok=False, reason=Reason.NOT_ZIP)
        if file_size > MAX_FILE_SIZE:
            return VerifyResult(ok=False, reason=Reason.TOO_LARGE)
        with open(source, "rb") as fp:
            return _verify_stream(fp, file_size)
    data = bytes(source)
    if len(data) > MAX_FILE_SIZE:
        return VerifyResult(ok=False, reason=Reason.TOO_LARGE)
    return _verify_stream(io.BytesIO(data), len(data))
