"""基于 expat 的流式 XML 核验：良构性、禁 DTD/实体、根元素与数据提取。

使用命名空间处理，元素名与具体前缀无关。
"""

from __future__ import annotations

from xml.parsers import expat

NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_OFFICE_DOCUMENT = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)


class XmlReject(Exception):
    """XML 层面拒绝。"""


class ContentTypes:
    """[Content_Types].xml 的解析结果。"""

    def __init__(self) -> None:
        self.defaults: dict[str, str] = {}
        self.overrides: dict[str, str] = {}


class Relationships:
    """_rels/.rels 的解析结果。"""

    def __init__(self) -> None:
        self.office_document_targets: list[tuple[str, bool]] = []


class _Sink:
    """流式 XML 校验器：接收解压数据块，结束时调用 close()。"""

    def __init__(self) -> None:
        self._parser = expat.ParserCreate(namespace_separator="}")
        self._parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        self._parser.StartDoctypeDeclHandler = self._forbidden
        self._parser.EntityDeclHandler = self._forbidden
        self._parser.NotationDeclHandler = self._forbidden
        self._parser.UnparsedEntityDeclHandler = self._forbidden
        self._done = False
        self._hook()

    def _forbidden(self, *args) -> None:
        raise XmlReject("DTD or entity declaration not allowed")

    def _hook(self) -> None:
        raise NotImplementedError

    def __call__(self, chunk: bytes) -> None:
        if self._done:
            raise XmlReject("data after xml stream end")
        try:
            self._parser.Parse(chunk, False)
        except expat.ExpatError as exc:
            raise XmlReject(f"malformed xml: {exc}") from exc

    def close(self) -> None:
        try:
            self._parser.Parse(b"", True)
        except expat.ExpatError as exc:
            raise XmlReject(f"truncated xml: {exc}") from exc
        self._done = True
        self._finish()

    def _finish(self) -> None:
        pass


class _ContentTypesSink(_Sink):
    def __init__(self, result: ContentTypes) -> None:
        self.result = result
        self._depth = 0
        super().__init__()

    def _hook(self) -> None:
        self._parser.StartElementHandler = self._start
        self._parser.EndElementHandler = self._end

    def _start(self, name: str, attrs: dict) -> None:
        if self._depth == 0:
            if name != NS_CT + "}Types":
                raise XmlReject("content types root element mismatch")
        elif name == NS_CT + "}Default":
            ext = attrs.get("Extension")
            ctype = attrs.get("ContentType")
            if not ext or not ctype:
                raise XmlReject("incomplete Default declaration")
            if ext in self.result.defaults:
                raise XmlReject("duplicate Default extension declaration")
            self.result.defaults[ext] = ctype
        elif name == NS_CT + "}Override":
            part = attrs.get("PartName")
            ctype = attrs.get("ContentType")
            if not part or not ctype:
                raise XmlReject("incomplete Override declaration")
            if part in self.result.overrides:
                raise XmlReject("duplicate Override declaration")
            self.result.overrides[part] = ctype
        self._depth += 1

    def _end(self, name: str) -> None:
        self._depth -= 1


class _RelationshipsSink(_Sink):
    def __init__(self, result: Relationships) -> None:
        self.result = result
        self._depth = 0
        super().__init__()

    def _hook(self) -> None:
        self._parser.StartElementHandler = self._start
        self._parser.EndElementHandler = self._end

    def _start(self, name: str, attrs: dict) -> None:
        if self._depth == 0:
            if name != NS_REL + "}Relationships":
                raise XmlReject("relationships root element mismatch")
        elif name == NS_REL + "}Relationship":
            if attrs.get("Type") == REL_OFFICE_DOCUMENT:
                target = attrs.get("Target")
                if not target:
                    raise XmlReject("officeDocument relationship without target")
                internal = attrs.get("TargetMode", "Internal") != "External"
                self.result.office_document_targets.append((target, internal))
        self._depth += 1

    def _end(self, name: str) -> None:
        self._depth -= 1


class _DocumentSink(_Sink):
    def __init__(self) -> None:
        self._seen_root = False
        super().__init__()

    def _hook(self) -> None:
        self._parser.StartElementHandler = self._start

    def _start(self, name: str, attrs: dict) -> None:
        if not self._seen_root:
            self._seen_root = True
            if name != NS_W + "}document":
                raise XmlReject("main document root element mismatch")

    def _finish(self) -> None:
        if not self._seen_root:
            raise XmlReject("empty main document")


def make_sink(kind: str, result_holder: dict) -> _Sink:
    """构造流式 XML 校验器；kind ∈ content_types / rels / document。"""
    if kind == "content_types":
        holder = ContentTypes()
        result_holder["content_types"] = holder
        return _ContentTypesSink(holder)
    if kind == "rels":
        holder = Relationships()
        result_holder["rels"] = holder
        return _RelationshipsSink(holder)
    if kind == "document":
        return _DocumentSink()
    raise ValueError(f"unknown sink kind: {kind}")
