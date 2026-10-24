"""Reading SQL, and running it where that is honest.

There is no PostgreSQL on most PCs, so a query is run on SQLite after a few translations of what the two share under
different names (``ILIKE``, ``::int``, ``STRING_AGG``...), and the rows are compared. Anything only Postgres has
(``DISTINCT ON``, ``JSONB``, ``GIN``, ``SKIP LOCKED``) is never run: it is looked for in the text, with comments and
string contents blanked out. The verdict says which of the two decided it.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agentfit.arena import text as tx

SQL_LANGS = {"sql", "postgresql", "postgres", "pgsql", "plpgsql", "psql"}
_KEYWORD = re.compile(r"^\s*(select|with|insert|update|delete|create|alter|drop|explain|values|table)\b", re.IGNORECASE)


def _scan(sql: str, keep_strings: bool, blank: bool):  # type: ignore[no-untyped-def]
    """Walk the SQL once, outside strings and comments. Yields (char, in_code, boundary) for every character."""
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            for _ in range(i, j):
                yield " ", False, False
            i = j
        elif ch == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                yield ("\n" if sql[k] == "\n" else " "), False, False
            i = j
        elif ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and sql[j + 1 : j + 2] == "'":
                    j += 2
                    continue
                if sql[j] == "'":
                    break
                j += 1
            j = min(j + 1, n)
            for k in range(i, j):
                inside = 0 < k - i < j - i - 1
                yield (" " if (blank and inside) else sql[k]), keep_strings, False
            i = j
        elif ch == '"':
            j = sql.find('"', i + 1)
            j = n if j == -1 else j + 1
            for k in range(i, j):
                yield sql[k], True, False
            i = j
        elif ch == "$":
            tag = re.match(r"\$(\w*)\$", sql[i:])
            if tag:
                end = sql.find(tag.group(0), i + len(tag.group(0)))
                j = n if end == -1 else end + len(tag.group(0))
                for k in range(i, j):
                    inside = len(tag.group(0)) <= k - i < j - i - len(tag.group(0))
                    yield (" " if (blank and inside) else sql[k]), keep_strings, False
                i = j
            else:
                yield ch, True, False
                i += 1
        elif ch == ";":
            yield ch, True, True
            i += 1
        else:
            yield ch, True, False
            i += 1


def split_statements(sql: str) -> list[str]:
    """The statements of a script, without their comments and without the semicolons."""
    statements, current = [], []
    for char, _code, boundary in _scan(sql, keep_strings=True, blank=False):
        if boundary:
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip()]


def blank_literals(sql: str, strings: bool = True) -> str:
    """The same text with comments, and by default the inside of strings, replaced by spaces: every position stays where it was."""
    return "".join(char for char, _code, _b in _scan(sql, keep_strings=True, blank=strings))


def extract_sql(reply: str) -> str | None:
    blocks = [b.code.strip() for b in tx.code_blocks(reply) if b.lang in SQL_LANGS]
    if not blocks:
        blocks = [b.code.strip() for b in tx.code_blocks(reply) if b.lang == "" and _KEYWORD.search(b.code)]
    if blocks:
        return "\n\n".join(b for b in blocks if b)
    return reply.strip() if _KEYWORD.search(reply) and "```" not in reply else None


# -- from Postgres to SQLite ----------------------------------------------------------------------------

_CAST_TYPES = {
    "int": "INTEGER", "integer": "INTEGER", "int2": "INTEGER", "int4": "INTEGER", "int8": "INTEGER", "smallint": "INTEGER", "bigint": "INTEGER",
    "bool": "INTEGER", "boolean": "INTEGER", "text": "TEXT", "varchar": "TEXT", "char": "TEXT", "character varying": "TEXT", "date": "TEXT",
    "timestamp": "TEXT", "timestamptz": "TEXT", "time": "TEXT", "json": "TEXT", "jsonb": "TEXT", "uuid": "TEXT",
    "numeric": "NUMERIC", "decimal": "NUMERIC", "float": "REAL", "float4": "REAL", "float8": "REAL", "real": "REAL", "double precision": "REAL",
}
_CAST_TAIL = re.compile(r"\s*::\s*(double precision|character varying|[A-Za-z_][A-Za-z_0-9]*)(\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?")


def _operand_start(sql: str, end: int) -> int:
    """Where the expression ending just before ``end`` begins: a name, a literal, or a call with its parentheses."""
    i = end
    while i > 0 and sql[i - 1] == " ":
        i -= 1
    if i > 0 and sql[i - 1] == ")":
        depth = 0
        while i > 0:
            i -= 1
            if sql[i] == ")":
                depth += 1
            elif sql[i] == "(":
                depth -= 1
                if depth == 0:
                    break
        while i > 0 and re.match(r"[\w.]", sql[i - 1]):
            i -= 1
        return i
    if i > 0 and sql[i - 1] == "'":
        j = sql.rfind("'", 0, i - 1)
        return max(j, 0)
    while i > 0 and re.match(r"[\w.\"]", sql[i - 1]):
        i -= 1
    return i


def to_sqlite(sql: str) -> str:
    out = re.sub(r"\bILIKE\b", "LIKE", sql, flags=re.IGNORECASE)
    out = re.sub(r"\bSTRING_AGG\s*\(", "GROUP_CONCAT(", out, flags=re.IGNORECASE)
    out = re.sub(r"\bGREATEST\s*\(", "MAX(", out, flags=re.IGNORECASE)
    out = re.sub(r"\bLEAST\s*\(", "MIN(", out, flags=re.IGNORECASE)
    out = re.sub(r"\bFETCH\s+(?:FIRST|NEXT)\s+(\d+)\s+ROWS?\s+ONLY\b", r"LIMIT \1", out, flags=re.IGNORECASE)
    while True:
        index = out.find("::")
        if index == -1:
            return out
        tail = _CAST_TAIL.match(out, index)
        if tail is None:
            return out
        start = _operand_start(out, index)
        kind = _CAST_TYPES.get(tail.group(1).lower(), tail.group(1).upper())
        out = out[:start] + f"CAST({out[start:index].strip()} AS {kind})" + out[tail.end():]


# -- CREATE TABLE and CREATE INDEX -----------------------------------------------------------------------

_TYPE_ALIASES = {"int": "integer", "int4": "integer", "int8": "bigint", "int2": "smallint", "bool": "boolean", "character varying": "varchar",
                 "timestamp with time zone": "timestamptz", "timestamp without time zone": "timestamp", "float8": "double precision", "decimal": "numeric", "serial4": "serial", "serial8": "bigserial"}
_CONSTRAINT_WORDS = re.compile(r"\b(not\s+null|null|default|primary|unique|references|check|generated|collate|constraint)\b", re.IGNORECASE)


@dataclass
class Column:
    name: str
    type: str = ""
    pk: bool = False
    not_null: bool = False
    unique: bool = False
    references: str | None = None
    check: bool = False
    default: bool = False
    identity: bool = False


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    table_unique: list[list[str]] = field(default_factory=list)
    table_pk: list[str] = field(default_factory=list)

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name.lower()), None)


@dataclass
class Index:
    table: str
    columns: list[str]
    method: str
    unique: bool
    name: str = ""


def _balanced(text: str, open_at: int) -> int:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_top(body: str) -> list[str]:
    parts, depth, current = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _name(token: str) -> str:
    return token.strip().strip('"').rsplit(".", 1)[-1].strip('"').lower()


def _type_of(rest: str) -> str:
    match = _CONSTRAINT_WORDS.search(rest)
    words = re.sub(r"\([^)]*\)", "", rest[: match.start()] if match else rest).strip().lower()
    words = re.sub(r"\s+", " ", words)
    return _TYPE_ALIASES.get(words, words)


def _columns_in(text: str) -> list[str]:
    """The names inside the first pair of parentheses of a table constraint: PRIMARY KEY (a, b)."""
    groups = re.findall(r"\(([^)]*)\)", text)
    return [_name(c) for c in groups[0].split(",")] if groups else []


def parse_create_tables(sql: str) -> list[Table]:
    blank = blank_literals(sql)
    tables: list[Table] = []
    for match in re.finditer(r"\bCREATE\s+(?:(?:GLOBAL\s+|LOCAL\s+)?(?:TEMP|TEMPORARY)\s+|UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)\s*\(", blank, re.IGNORECASE):
        open_at = match.end() - 1
        close = _balanced(blank, open_at)
        if close == -1:
            continue
        table = Table(name=_name(match.group(1)))
        for item in _split_top(sql[open_at + 1 : close]):
            lowered = item.lower()
            body = re.sub(r"^constraint\s+\S+\s+", "", lowered)
            if body.startswith("primary key"):
                table.table_pk = _columns_in(body)
                for name in table.table_pk:
                    if (c := table.column(name)):
                        c.pk = c.not_null = True
                continue
            if body.startswith("unique"):
                found = _columns_in(body)
                table.table_unique.append(found)
                if len(found) == 1 and (c := table.column(found[0])):
                    c.unique = True
                continue
            if body.startswith("foreign key"):
                ref = re.search(r"references\s+([\w.\"]+)", body)
                for name in _columns_in(body):
                    if ref and (c := table.column(name)):
                        c.references = _name(ref.group(1))
                continue
            if body.startswith(("check", "exclude", "like")):
                continue
            name_match = re.match(r'\s*("[^"]+"|\w+)\s*(.*)$', item, re.DOTALL)
            if not name_match:
                continue
            rest = name_match.group(2)
            ref = re.search(r"\breferences\s+([\w.\"]+)", rest, re.IGNORECASE)
            column = Column(
                name=_name(name_match.group(1)), type=_type_of(rest),
                pk=bool(re.search(r"\bprimary\s+key\b", rest, re.IGNORECASE)),
                not_null=bool(re.search(r"\bnot\s+null\b", rest, re.IGNORECASE)),
                unique=bool(re.search(r"\bunique\b", rest, re.IGNORECASE)),
                references=_name(ref.group(1)) if ref else None,
                check=bool(re.search(r"\bcheck\s*\(", rest, re.IGNORECASE)),
                default=bool(re.search(r"\bdefault\b", rest, re.IGNORECASE)),
                identity=bool(re.search(r"\bgenerated\b.*\bidentity\b", rest, re.IGNORECASE)),
            )
            if column.pk:
                column.not_null = True
            table.columns.append(column)
        tables.append(table)
    return tables


def parse_indexes(sql: str) -> list[Index]:
    blank = blank_literals(sql)
    found: list[Index] = []
    pattern = re.compile(r"\bCREATE\s+(UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(?:(?!ON\b)(\w+)\s+)?ON\s+(?:ONLY\s+)?([\w.\"]+)\s*(?:USING\s+(\w+)\s*)?\(", re.IGNORECASE)
    for match in pattern.finditer(blank):
        open_at = match.end() - 1
        close = _balanced(blank, open_at)
        if close == -1:
            continue
        columns = []
        for part in _split_top(sql[open_at + 1 : close]):
            first = re.match(r'\s*("[^"]+"|[\w]+|\(.*\))', part)
            columns.append(_name(first.group(1)) if first else part.lower())
        found.append(Index(table=_name(match.group(3)), columns=columns, method=(match.group(4) or "btree").lower(), unique=bool(match.group(1)), name=(match.group(2) or "").lower()))
    return found


# -- running a query -------------------------------------------------------------------------------------


class SqlRunError(Exception):
    pass


def _deny(action: int, *_: Any) -> int:
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def first_word(statement: str) -> str:
    match = re.match(r"\s*(\w+)", statement)
    return match.group(1).lower() if match else ""


def normalize_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    def one(v: Any) -> Any:
        if isinstance(v, float):
            return round(v, 6)
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, bytes):
            return v.hex()
        return v

    return [tuple(one(v) for v in row) for row in rows]


def run_on_sqlite(setup: str, statements: list[str], then: str | None = None, *, seconds: float = 5.0) -> list[tuple[Any, ...]]:
    """Set up an empty in-memory database, run the statements, and return the rows of the last one that returns any (or of ``then``)."""
    con = sqlite3.connect(":memory:")
    timer = threading.Timer(seconds, con.interrupt)
    try:
        con.executescript(setup)
        con.set_authorizer(_deny)
        timer.start()
        rows: list[tuple[Any, ...]] = []
        for statement in statements:
            cursor = con.execute(to_sqlite(statement))
            if cursor.description is not None:
                rows = cursor.fetchmany(10_000)
        if then is not None:
            rows = con.execute(to_sqlite(then)).fetchmany(10_000)
        return normalize_rows(rows)
    except sqlite3.Error as exc:
        raise SqlRunError(str(exc)) from exc
    finally:
        timer.cancel()
        con.close()


def same_rows(found: list[tuple[Any, ...]], expected: list[list[Any]], ordered: bool) -> tuple[bool, str | None]:
    """Whether the rows are the expected ones, and if not, why in one word: columns, rows, order, values."""
    want = normalize_rows([tuple(r) for r in expected])
    if found and want and len(found[0]) != len(want[0]):
        return False, "columns"
    if len(found) != len(want):
        return False, "rows"
    if ordered:
        return (found == want), (None if found == want else ("order" if Counter(found) == Counter(want) else "values"))
    return (Counter(found) == Counter(want)), (None if Counter(found) == Counter(want) else "values")
