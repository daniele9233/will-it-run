"""PostgreSQL questions: queries run on fixed data and compared; schemas and Postgres-only syntax read from the text."""

from __future__ import annotations

import re
from typing import Any

from agentfit.arena import sqlcheck
from agentfit.arena.model import FORMAT, WRONG, Answer, Check, Question, Reason, Verdict, why
from agentfit.arena.verifiers import Context, register
from agentfit.arena.verifiers.basic import shorten

# What other databases write and Postgres does not: a question that says «PostgreSQL» does not accept it.
_FOREIGN = [
    ("`", why("It quotes names with backticks: PostgreSQL uses double quotes.")),
    (r"(?i)\bLIMIT\s+\d+\s*,\s*\d+", why("LIMIT with a comma is MySQL: PostgreSQL writes LIMIT n OFFSET m.")),
    (r"(?i)\bAUTO_INCREMENT\b", why("AUTO_INCREMENT is MySQL: PostgreSQL uses SERIAL or GENERATED ... AS IDENTITY.")),
    (r"(?i)\bSELECT\s+TOP\s+\d+", why("TOP n is SQL Server: PostgreSQL uses LIMIT.")),
    (r"(?i)\b(?:IFNULL|ISNULL|NVL)\s*\(", why("IFNULL, ISNULL and NVL are other databases': PostgreSQL uses COALESCE.")),
    (r"(?i)\b(?:ROWNUM|SYSDATE|NOLOCK)\b", why("ROWNUM, SYSDATE and NOLOCK belong to Oracle or SQL Server, not to PostgreSQL.")),
    (r"(?i)\bDECODE\s*\(", why("DECODE is Oracle's: PostgreSQL uses CASE.")),
]


def _style_checks(spec: dict[str, Any], clean: str) -> list[Check]:
    checks: list[Check] = []
    for pattern in spec.get("must_have", []):
        found = re.search(pattern, clean) is not None
        shown = spec.get("labels", {}).get(pattern) or pattern
        checks.append(Check(found, why("It uses {what}.", what=shown) if found else why("It does not use {what}.", what=shown)))
    for pattern in spec.get("must_not", []):
        found = re.search(pattern, clean) is not None
        shown = spec.get("labels", {}).get(pattern) or pattern
        checks.append(Check(not found, why("It avoids {what}.", what=shown) if not found else why("It uses {what}, which it should not.", what=shown)))
    if spec.get("dialect") == "postgres":
        for pattern, reason in _FOREIGN:
            if re.search(pattern, clean):
                checks.append(Check(False, reason))
    return checks


def _finish(checks: list[Check], method: str, extracted: str, question: Question, *, essential: bool = True) -> Verdict:
    good = sum(1 for c in checks if c.ok)
    if not checks or good == len(checks):
        return Verdict.passed(checks=checks, extracted=extracted, expected=question.expected, method=method)
    failing = [c for c in checks if not c.ok]
    reason: Reason = failing[0].reason if len(failing) == 1 else why("{n} of {total} requirements are not met.", n=len(failing), total=len(checks))
    if good == 0 or (essential and not checks[0].ok):
        return Verdict.failed(WRONG, reason, checks=checks, extracted=extracted, expected=question.expected, method=method)
    return Verdict.partial(good / len(checks), reason, checks=checks, extracted=extracted, expected=question.expected, method=method)


@register("sql")
def sql(spec: dict[str, Any], answer: Answer, question: Question, ctx: Context) -> Verdict:
    mode = spec.get("mode", "query")
    script = sqlcheck.extract_sql(answer.text)
    if script is None:
        return Verdict.failed(FORMAT, why("No SQL was found in the reply."), extracted=shorten(answer.text), expected=question.expected, method="sqlite", format_ok=False)
    # Comments never count; the inside of a string does not either, unless the question is about a literal («->> 'type'»).
    clean = sqlcheck.blank_literals(script, strings=not spec.get("literals"))
    shown = shorten(" ".join(script.split()), 300)
    if mode == "features":
        return _finish(_style_checks(spec, clean), "structure", shown, question, essential=False)
    if mode == "ddl":
        return _ddl(spec, script, clean, shown, question)
    statements = [s for s in sqlcheck.split_statements(script) if sqlcheck.first_word(s) in ("select", "with", "values", "table", "insert", "update", "delete")]
    if mode == "query":
        selects = [s for s in statements if sqlcheck.first_word(s) in ("select", "with", "values", "table")]
        chosen, then = selects[-1:], None
    else:
        chosen = [s for s in statements if sqlcheck.first_word(s) in ("insert", "update", "delete", "with")]
        then = spec["then"]
    if not chosen:
        return Verdict.failed(FORMAT, why("The reply has no statement of the kind asked for."), extracted=shown, expected=question.expected, method="sqlite", format_ok=False)
    try:
        rows = sqlcheck.run_on_sqlite(spec["setup"], chosen, then)
    except sqlcheck.SqlRunError as exc:
        return Verdict.failed(WRONG, why("The database says: {error}", error=str(exc)), checks=[Check(False, why("The query does not run: {error}", error=str(exc)))],
                              extracted=shown, expected=question.expected, method="sqlite")
    ok, difference = sqlcheck.same_rows(rows, spec["expect"], bool(spec.get("ordered")))
    expected_text = question.expected or shorten(str(spec["expect"][:3]), 120)
    found_text = shorten(str([list(r) for r in rows[:3]]), 120)
    if not ok:
        if difference == "columns":
            reason = why("The query returns {n} columns, expected {m}.", n=len(rows[0]) if rows else 0, m=len(spec["expect"][0]))
        elif difference == "rows":
            reason = why("The query returns {n} rows, expected {m}.", n=len(rows), m=len(spec["expect"]))
        elif difference == "order":
            reason = why("The rows are right but not in the order asked for.")
        else:
            reason = why("Expected rows like {expected}, found {found}.", expected=shorten(str(spec["expect"][:3]), 120), found=found_text)
        return Verdict.failed(WRONG, reason, checks=[Check(False, reason)], extracted=shown, expected=expected_text, method="sqlite")
    checks = [Check(True, why("The query returns the expected {n} rows.", n=len(rows))), *_style_checks(spec, clean)]
    return _finish(checks, "sqlite", shown, question)


_SYNONYMS = {"int": "integer", "int4": "integer", "int8": "bigint"}


def _ddl(spec: dict[str, Any], script: str, clean: str, shown: str, question: Question) -> Verdict:
    tables = {t.name: t for t in sqlcheck.parse_create_tables(script)}
    checks: list[Check] = []
    for wanted in spec.get("tables", []):
        name = wanted["name"].lower()
        table = tables.get(name)
        if table is None:
            reason = why("The table {name} is not created.", name=wanted["name"])
            return Verdict.failed(WRONG, reason, checks=[Check(False, reason)], extracted=shown, expected=question.expected, method="structure")
        for column_name, rule in wanted.get("columns", {}).items():
            column = table.column(column_name)
            if column is None:
                checks.append(Check(False, why("{table}: the column {column} is missing.", table=name, column=column_name)))
                continue
            good = True
            if "type" in rule:
                allowed = [_SYNONYMS.get(t.lower(), t.lower()) for t in (rule["type"] if isinstance(rule["type"], list) else [rule["type"]])]
                if _SYNONYMS.get(column.type, column.type) not in allowed:
                    good = False
                    checks.append(Check(False, why("{table}.{column}: the type is {found}, expected one of {allowed}.", table=name, column=column_name, found=column.type or "none", allowed=", ".join(allowed))))
            for flag, label in (("pk", "PRIMARY KEY"), ("not_null", "NOT NULL"), ("unique", "UNIQUE")):
                if rule.get(flag) and not getattr(column, flag):
                    good = False
                    checks.append(Check(False, why("{table}.{column}: {label} is missing.", table=name, column=column_name, label=label)))
            if rule.get("references") and column.references != str(rule["references"]).lower():
                good = False
                checks.append(Check(False, why("{table}.{column}: it should reference {ref}.", table=name, column=column_name, ref=rule["references"])))
            if rule.get("check") and not column.check:
                good = False
                checks.append(Check(False, why("{table}.{column}: a CHECK constraint is missing.", table=name, column=column_name)))
            if rule.get("default") and not column.default:
                good = False
                checks.append(Check(False, why("{table}.{column}: a DEFAULT is missing.", table=name, column=column_name)))
            if good:
                checks.append(Check(True, why("{table}.{column} is as asked.", table=name, column=column_name)))
    indexes = sqlcheck.parse_indexes(script)
    for wanted_index in spec.get("indexes", []):
        table_name = wanted_index["table"].lower()
        columns = [c.lower() for c in wanted_index["columns"]]
        match = next((i for i in indexes if i.table == table_name and i.columns[: len(columns)] == columns and (not wanted_index.get("using") or i.method == wanted_index["using"].lower())), None)
        label = f"{table_name}({', '.join(columns)})" + (f" USING {wanted_index['using']}" if wanted_index.get("using") else "")
        checks.append(Check(match is not None, why("There is an index on {what}.", what=label) if match else why("There is no index on {what}.", what=label)))
    checks.extend(_style_checks(spec, clean))
    return _finish(checks, "structure", shown, question, essential=False)
