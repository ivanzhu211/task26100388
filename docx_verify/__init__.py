"""DOCX intake verification library."""
from .errors import Reason
from .verifier import VerifyResult, verify_docx

__all__ = ["Reason", "VerifyResult", "verify_docx"]
