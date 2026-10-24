"""A reader for the structure of HCL, the language of Terraform files: blocks, their labels, their attributes.

It does not evaluate anything. It finds out where blocks begin and end, what they are called and what their attributes
say as text, so an answer can be checked for «has a variable called names» and so a broken file is caught when Terraform
is not installed. Comments, strings with ``${...}`` inside, heredocs and nested blocks are understood.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field


class HclError(Exception):
    def __init__(self, message: str, line: int) -> None:
        super().__init__(f"{message} (line {line})")
        self.message = message
        self.line = line


@dataclass
class Block:
    type: str
    labels: list[str]
    attrs: dict[str, str] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)
    line: int = 1


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_HEREDOC = re.compile(r"<<-?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\r?\n")


def walk(blocks: list[Block]) -> Iterator[Block]:
    for block in blocks:
        yield block
        yield from walk(block.blocks)


class _Reader:
    def __init__(self, text: str) -> None:
        self.s, self.n, self.i = text, len(text), 0

    def line(self, at: int | None = None) -> int:
        return self.s.count("\n", 0, self.i if at is None else at) + 1

    def skip_inline(self) -> None:
        while self.i < self.n and self.s[self.i] in " \t\r":
            self.i += 1

    def skip_space(self) -> None:
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "#" or self.s.startswith("//", self.i):
                end = self.s.find("\n", self.i)
                self.i = self.n if end == -1 else end
            elif self.s.startswith("/*", self.i):
                end = self.s.find("*/", self.i + 2)
                if end == -1:
                    raise HclError("A comment is not closed", self.line())
                self.i = end + 2
            else:
                return

    def string(self) -> str:
        start = self.i
        self.i += 1
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
            elif c == '"':
                self.i += 1
                return self.s[start : self.i]
            elif c == "\n":
                raise HclError("A string is not closed", self.line())
            elif self.s.startswith("$${", self.i) or self.s.startswith("%%{", self.i):
                self.i += 3
            elif c in "$%" and self.s.startswith("{", self.i + 1):
                self.i += 2
                self.braced()
            else:
                self.i += 1
        raise HclError("A string is not closed", self.line())

    def braced(self) -> None:
        """Skip to the end of a ``${ ... }`` sequence whose opening brace has been read."""
        depth = 1
        while self.i < self.n:
            c = self.s[self.i]
            if c == '"':
                self.string()
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    self.i += 1
                    return
            self.i += 1
        raise HclError("A ${ sequence is not closed", self.line())

    def heredoc(self) -> bool:
        match = _HEREDOC.match(self.s, self.i)
        if not match:
            return False
        tag = match.group(1)
        pos = match.end()
        while pos < self.n:
            end = self.s.find("\n", pos)
            end = self.n if end == -1 else end
            if self.s[pos:end].strip() == tag:
                self.i = end
                return True
            pos = end + 1
        raise HclError(f"The heredoc {tag} is not closed", self.line())

    def expression(self) -> str:
        start, depth = self.i, 0
        while self.i < self.n:
            c = self.s[self.i]
            if c == '"':
                self.string()
                continue
            if c == "<" and self.heredoc():
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif c == "#" or self.s.startswith("//", self.i):
                if depth == 0:
                    break
                end = self.s.find("\n", self.i)
                self.i = self.n if end == -1 else end
                continue
            elif self.s.startswith("/*", self.i):
                end = self.s.find("*/", self.i + 2)
                if end == -1:
                    raise HclError("A comment is not closed", self.line())
                self.i = end + 2
                continue
            elif c == "\n" and depth == 0:
                break
            self.i += 1
        if depth > 0 and self.i >= self.n:
            raise HclError("A bracket is not closed", self.line(start))
        return self.s[start : self.i].strip()

    def body(self, opened_at: int | None) -> tuple[dict[str, str], list[Block]]:
        attrs: dict[str, str] = {}
        blocks: list[Block] = []
        while True:
            self.skip_space()
            if self.i >= self.n:
                if opened_at is not None:
                    raise HclError("A block is not closed", self.line(opened_at))
                return attrs, blocks
            if self.s[self.i] == "}":
                if opened_at is None:
                    raise HclError("A closing brace has no block to close", self.line())
                self.i += 1
                return attrs, blocks
            ident = _IDENT.match(self.s, self.i)
            if not ident:
                raise HclError(f"Unexpected {self.s[self.i]!r}", self.line())
            name, line = ident.group(0), self.line()
            self.i = ident.end()
            self.skip_inline()
            if self.s.startswith("=", self.i) and not self.s.startswith("==", self.i):
                self.i += 1
                self.skip_inline()
                attrs[name] = self.expression()
                continue
            labels: list[str] = []
            while True:
                self.skip_inline()
                if self.i >= self.n:
                    raise HclError(f"The block {name} has no body", self.line())
                c = self.s[self.i]
                if c == '"':
                    labels.append(self.string()[1:-1])
                elif c == "{":
                    break
                elif _IDENT.match(self.s, self.i):
                    word = _IDENT.match(self.s, self.i)
                    labels.append(word.group(0))  # type: ignore[union-attr]
                    self.i = word.end()  # type: ignore[union-attr]
                else:
                    raise HclError(f"Expected an attribute or a block after {name!r}", self.line())
            opened = self.i
            self.i += 1
            child_attrs, child_blocks = self.body(opened)
            blocks.append(Block(name, labels, child_attrs, child_blocks, line))


def parse(text: str) -> list[Block]:
    """The top-level blocks of a file. Raises ``HclError`` with a line number when it cannot be read."""
    reader = _Reader(text)
    attrs, blocks = reader.body(None)
    if attrs:
        # Attributes outside any block are not Terraform: it only has blocks at the top.
        name = next(iter(attrs))
        raise HclError(f"The attribute {name!r} is outside any block", text[: text.find(name)].count("\n") + 1)
    return blocks


def blank(text: str, strings: bool = True) -> str:
    """The text with comments (and, by default, the inside of strings and heredocs) replaced by spaces. Same length."""
    out = list(text)
    i, n = 0, len(text)

    def wipe(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]
        if c == "#" or text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end == -1 else end
            wipe(i, end)
            i = end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            wipe(i, end)
            i = end
        elif c == '"':
            reader = _Reader(text)
            reader.i = i
            try:
                reader.string()
                j = reader.i
            except HclError:
                end = text.find("\n", i)
                j = n if end == -1 else end
            if strings:
                wipe(i + 1, j - 1)
            i = j
        elif c == "<":
            match = _HEREDOC.match(text, i)
            if match:
                tag = match.group(1)
                pos = match.end()
                end = n
                while pos < n:
                    stop = text.find("\n", pos)
                    stop = n if stop == -1 else stop
                    if text[pos:stop].strip() == tag:
                        end = pos
                        break
                    pos = stop + 1
                if strings:
                    wipe(match.end(), end)
                i = end
            else:
                i += 1
        else:
            i += 1
    return "".join(out)
