"""A small PDF writer and the reader that goes with it.

The PDF questions need real PDF files: an invoice, a contract, a report. They are written here, byte for byte the same
every time (no dates, no ids), with one monospaced font so a table keeps its columns. The text is then read *back* from the
file by ``extract_pages``, the way an application would, and that text is what the model is given.

The reader is deliberately modest: it finds the pages, inflates a stream when it is compressed with Flate, and collects
what the text-showing operators print, in Latin-1/WinAnsi. It does not understand embedded subset fonts or scanned images.
For those, the text comes out wrong or empty, and nothing here pretends otherwise.
"""

from __future__ import annotations

import re
import zlib

LINES_PER_PAGE = 48
_TOP, _LEADING, _SIZE, _LEFT = 700, 14, 12, 72


def _escape(value: str) -> bytes:
    raw = value.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def build(pages: list[list[str]], title: str = "Document") -> bytes:
    """A PDF with one page per list of lines (at most ``LINES_PER_PAGE`` each)."""
    if any(len(lines) > LINES_PER_PAGE for lines in pages):
        raise ValueError(f"a page holds at most {LINES_PER_PAGE} lines")
    count = len(pages)
    page_numbers = [4 + 2 * i for i in range(count)]
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % n for n in page_numbers) + b"] /Count %d >>" % count,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
    ]
    for i, lines in enumerate(pages):
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (page_numbers[i] + 1))
        content = b"\n".join(b"BT /F1 %d Tf %d %d Td (" % (_SIZE, _LEFT, _TOP - _LEADING * n) + _escape(line) + b") Tj ET" for n, line in enumerate(lines))
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objects.append(b"<< /Title (" + _escape(title) + b") /Producer (Will It Run Arena) >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, len(objects), xref)
    return bytes(out)


def table_lines(rows: list[list[str]], gap: int = 3) -> list[str]:
    """Rows of cells as lines of monospaced columns: text on the left, numbers on the right."""
    widths = [max(len(row[i]) for row in rows if i < len(row)) for i in range(max(len(r) for r in rows))]
    numeric = re.compile(r"^[\d.,\s€$%+-]+$")
    lines = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            right = bool(numeric.match(cell)) and i > 0
            cells.append(cell.rjust(widths[i]) if right else cell.ljust(widths[i]))
        lines.append((" " * gap).join(cells).rstrip())
    return lines


# -- reading ----------------------------------------------------------------------------------------

_OBJ = re.compile(rb"(\d+)\s+\d+\s+obj\b(.*?)\bendobj", re.DOTALL)
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_REF = re.compile(rb"(\d+)\s+\d+\s+R")
_TOKEN = re.compile(
    rb"""(?P<str>\((?:\\.|[^\\()])*\))|(?P<hex><[0-9A-Fa-f\s]*>)|(?P<arr>\[(?:\((?:\\.|[^\\()])*\)|[^\]])*\])|"""
    rb"""(?P<num>[-+]?\d*\.?\d+)|(?P<name>/[^\s/<>\[\]()]+)|(?P<op>[A-Za-z'"*]+)""",
    re.DOTALL,
)
_ESCAPES = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f", ord("("): b"(", ord(")"): b")", ord("\\"): b"\\"}


def _unescape(raw: bytes) -> str:
    body = raw[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        byte = body[i]
        if byte == 0x5C and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt]
                i += 2
                continue
            octal = re.match(rb"[0-7]{1,3}", body[i + 1 :])
            if octal:
                out.append(int(octal.group(0), 8) & 0xFF)
                i += 1 + len(octal.group(0))
                continue
            i += 1
            continue
        out.append(byte)
        i += 1
    return out.decode("cp1252", errors="replace")


def _hex_text(raw: bytes) -> str:
    digits = re.sub(rb"\s", b"", raw[1:-1])
    if len(digits) % 2:
        digits += b"0"
    return bytes.fromhex(digits.decode()).decode("cp1252", errors="replace")


def _show_text(content: bytes) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    operands: list[tuple[str, bytes]] = []

    def flush() -> None:
        if current:
            lines.append("".join(current))
            current.clear()

    for match in _TOKEN.finditer(content):
        kind = match.lastgroup or ""
        token = match.group(0)
        if kind != "op":
            operands.append((kind, token))
            continue
        op = token.decode("latin-1")
        if op == "Tj" and operands:
            current.append(_unescape(operands[-1][1]) if operands[-1][0] == "str" else _hex_text(operands[-1][1]))
        elif op == "TJ" and operands:
            for piece in _TOKEN.finditer(operands[-1][1][1:-1]):
                if piece.lastgroup == "str":
                    current.append(_unescape(piece.group(0)))
                elif piece.lastgroup == "hex":
                    current.append(_hex_text(piece.group(0)))
                elif piece.lastgroup == "num" and float(piece.group(0)) < -200:
                    current.append(" ")
        elif op in ("'", '"') and operands:
            flush()
            current.append(_unescape(operands[-1][1]) if operands[-1][0] == "str" else "")
        elif op in ("Td", "TD") and len(operands) >= 2:
            if float(operands[-1][1]) != 0:
                flush()
        elif op in ("T*", "ET"):
            flush()
        operands.clear()
    flush()
    return lines


def _objects(data: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    found: dict[int, tuple[bytes, bytes | None]] = {}
    for match in _OBJ.finditer(data):
        body = match.group(2)
        stream = _STREAM.search(body)
        head = body[: stream.start()] if stream else body
        payload = stream.group(1) if stream else None
        if payload is not None and b"/FlateDecode" in head:
            try:
                payload = zlib.decompress(payload)
            except zlib.error:
                payload = None
        found[int(match.group(1))] = (head, payload)
    return found


def _page_order(objects: dict[int, tuple[bytes, bytes | None]]) -> list[int]:
    def walk(number: int) -> list[int]:
        head = objects.get(number, (b"", None))[0]
        if re.search(rb"/Type\s*/Pages\b", head):
            kids = re.search(rb"/Kids\s*\[(.*?)\]", head, re.DOTALL)
            return [leaf for ref in (_REF.findall(kids.group(1)) if kids else []) for leaf in walk(int(ref))]
        return [number] if re.search(rb"/Type\s*/Page\b", head) else []

    roots = [n for n, (head, _s) in objects.items() if re.search(rb"/Type\s*/Pages\b", head) and b"/Parent" not in head]
    ordered = [leaf for root in sorted(roots) for leaf in walk(root)]
    if ordered:
        return ordered
    return sorted(n for n, (head, _s) in objects.items() if re.search(rb"/Type\s*/Page\b", head))


def extract_pages(data: bytes) -> list[list[str]]:
    """The text of each page, a line at a time. Raises ValueError for something that is not a PDF."""
    if not data.lstrip().startswith(b"%PDF-"):
        raise ValueError("this is not a PDF file")
    objects = _objects(data)
    pages: list[list[str]] = []
    for number in _page_order(objects):
        head = objects[number][0]
        contents = re.search(rb"/Contents\s*(\[(.*?)\]|(\d+)\s+\d+\s+R)", head, re.DOTALL)
        refs = [int(r) for r in _REF.findall(contents.group(2))] if contents and contents.group(2) else ([int(contents.group(3))] if contents and contents.group(3) else [])
        content = b"\n".join(objects[r][1] or b"" for r in refs if r in objects)
        pages.append(_show_text(content))
    if not pages:
        raise ValueError("no pages were found in this PDF")
    return pages


def page_count(data: bytes) -> int:
    return len(extract_pages(data))


def extract_text(data: bytes) -> str:
    """All the text, pages separated by a form feed on a line of its own."""
    return "\n\f\n".join("\n".join(lines) for lines in extract_pages(data))
