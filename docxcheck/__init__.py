"""DOCX 入库核验库：确认 ZIP 容器完整且包含可识别的 OOXML Transitional 主文档。"""

from docxcheck._ooxml import Verdict, verify_docx

__all__ = ["Verdict", "verify_docx"]
