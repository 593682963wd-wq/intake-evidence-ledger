"""作者：Wangyuan；日期：2026-10-29。职责：有预算的PDF字面字符串提取；不处理压缩流、字体映射、图片或OCR。"""
import re
import time
MAX_PARSER_CPU_SECONDS = 0.5
MAX_TEXT_CHARS = 200_000
MAX_PDF_NESTING_DEPTH = 32
MAX_DOCUMENT_XML_BYTES = 5_000_000
MAX_PDF_LITERAL_COUNT = 10_000
_ACTIVE_DOCUMENT_MARKERS = (b'/javascript', b'/js', b'/launch', b'/embeddedfile', b'/openaction')

class _RejectedInput(ValueError):
    """保留可观测的拒绝原因，不返回原输入内容。"""
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.status = code, status

class _ParserBudget:
    """为解析循环提供可观察的 CPU 时间上限。"""

    def __init__(self) -> None:
        self.started = time.process_time()
        self.operations = 0

    def check(self, operations: int = 1) -> None:
        self.operations += operations
        if (
            self.operations % 256 == 0
            and time.process_time() - self.started > MAX_PARSER_CPU_SECONDS
        ):
            raise _RejectedInput("PARSER_TIMEOUT", "文档解析超过 CPU 时间上限")

    def finish(self) -> None:
        if time.process_time() - self.started > MAX_PARSER_CPU_SECONDS:
            raise _RejectedInput("PARSER_TIMEOUT", "文档解析超过 CPU 时间上限")

def _decode_pdf_literal(value: bytes, budget: _ParserBudget) -> str:
    result = bytearray()
    index = 0
    while index < len(value):
        budget.check()
        if len(result) > MAX_TEXT_CHARS * 4:
            raise _RejectedInput("DOCUMENT_TEXT_TOO_LONG", "PDF 文本超过字符上限", 413)
        if value[index] != 0x5C:
            result.append(value[index])
            index += 1
            continue
        index += 1
        if index >= len(value):
            break
        escaped = value[index]
        simple = {
            ord("n"): 0x0A,
            ord("r"): 0x0D,
            ord("t"): 0x09,
            ord("b"): 0x08,
            ord("f"): 0x0C,
        }
        if escaped in b"\n\r":
            index += 1
            if escaped == 0x0D and index < len(value) and value[index] == 0x0A: index += 1
            continue
        if escaped in simple:
            result.append(simple[escaped])
            index += 1
        elif escaped in b"()\\":
            result.append(escaped)
            index += 1
        elif 0x30 <= escaped <= 0x37:
            octal = bytes([escaped])
            index += 1
            for _ in range(2):
                budget.check()
                if index >= len(value) or not 0x30 <= value[index] <= 0x37:
                    break
                octal += bytes([value[index]])
                index += 1
            result.append(int(octal, 8) & 255)
        else:
            result.append(escaped)
            index += 1
    if bytes(result).startswith(b"\xfe\xff"):
        return bytes(result)[2:].decode("utf-16-be")
    try:
        return bytes(result).decode("utf-8")
    except UnicodeDecodeError:
        return bytes(result).decode("latin-1", errors="replace")

def _scan_pdf_literal(
    data: bytes,
    start: int,
    budget: _ParserBudget,
) -> tuple[bytes, int]:
    """线性扫描一个 PDF 字符串，避免重叠正则造成回溯爆炸。"""

    index = start + 1
    depth = 1
    raw = bytearray()
    while index < len(data):
        budget.check()
        current = data[index]
        if current == 0x5C:
            raw.append(current)
            index += 1
            if index >= len(data):
                raise _RejectedInput("CORRUPT_FILE", "PDF 字符串转义未闭合")
            raw.append(data[index])
            index += 1
            continue
        if current == 0x28:
            depth += 1
            if depth > MAX_PDF_NESTING_DEPTH:
                raise _RejectedInput("PARSER_LIMIT", "PDF 字符串嵌套超过上限")
            raw.append(current)
            index += 1
            continue
        if current == 0x29:
            depth -= 1
            index += 1
            if depth == 0:
                return bytes(raw), index
            raw.append(current)
            continue
        raw.append(current)
        index += 1
        if len(raw) > MAX_DOCUMENT_XML_BYTES:
            raise _RejectedInput("DOCUMENT_TEXT_TOO_LONG", "PDF 字符串超过字节上限", 413)
    raise _RejectedInput("CORRUPT_FILE", "PDF 字符串未闭合")

def read_pdf_literals(data: bytes) -> str:
    """只抽取未压缩字面字符串，结果不保证排版顺序或正文完整性。"""
    if not data or len(data) > 5_000_000:
        raise _RejectedInput("FILE_TOO_LARGE", "PDF为空或超过5MB预算")
    if re.search(rb"/(?:Filter|ObjStm|ToUnicode)\b", data, re.I) or re.search(rb"(?<!<)<[0-9A-Fa-f\s]+>(?!>)", data):
        raise _RejectedInput("UNSUPPORTED_PDF", "不支持压缩、对象流、字体映射或十六进制字符串；请提供校对后的文本")
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-4096:]:
        raise _RejectedInput("CORRUPT_FILE", "PDF 文件头或结束标记无效")
    lowered = data.lower()
    if b"/encrypt" in lowered:
        raise _RejectedInput("ENCRYPTED_FILE", "PDF 文件已加密")
    if any(marker in lowered for marker in _ACTIVE_DOCUMENT_MARKERS):
        raise _RejectedInput("EMBEDDED_OBJECT", "PDF 含活动脚本或启动对象，已拒绝")

    budget = _ParserBudget()
    parts: list[str] = []
    total_chars = 0
    literal_count = 0
    index = 0
    while index < len(data):
        budget.check()
        if data[index] != 0x28:
            index += 1
            continue
        literal_count += 1
        if literal_count > MAX_PDF_LITERAL_COUNT:
            raise _RejectedInput("PARSER_LIMIT", "PDF 字符串数量超过上限")
        raw_literal, index = _scan_pdf_literal(data, index, budget)
        decoded = _decode_pdf_literal(raw_literal, budget).strip()
        if decoded:
            total_chars += len(decoded)
            if total_chars > MAX_TEXT_CHARS:
                raise _RejectedInput("DOCUMENT_TEXT_TOO_LONG", "PDF 文本超过字符上限", 413)
            parts.append(decoded)
    budget.finish()
    return "\n".join(parts).strip()
