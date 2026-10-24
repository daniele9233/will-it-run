"""PostgreSQL questions: queries are run on fixed data (in SQLite, after a few honest translations) and the rows compared;
what only Postgres has is checked by reading the SQL."""

from __future__ import annotations

from agentfit.arena import sqlcheck
from agentfit.arena.model import Answer, Question
from agentfit.arena.verifiers import Context, verify

SETUP = """
CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT, dept TEXT, salary INTEGER);
INSERT INTO employees VALUES (1,'Ada','eng',120),(2,'Bob','eng',100),(3,'Cleo','ops',90),(4,'Dan','ops',95),(5,'Eve','hr',70);
"""


def q(check: dict) -> Question:
    return Question(id="s.001", category="sql", title="t", lang="en", prompt="p", check=check, expected="", reference="")


def run(check: dict, text: str, finish: str = "stop"):
    return verify(q(check), Answer(text=text, raw=text, finish_reason=finish), Context())


def fenced(sql: str) -> str:
    return f"Here is the query:\n```sql\n{sql}\n```\nThis groups by department."


AVG = {"type": "sql", "mode": "query", "setup": SETUP, "expect": [["eng", 110.0], ["hr", 70.0], ["ops", 92.5]], "dialect": "postgres"}


# -- reading SQL ----------------------------------------------------------------------------------------


def test_statements_are_split_outside_strings_comments_and_dollar_quotes():
    sql = "SELECT 'a;b'; -- not; a split\nSELECT 2; /* x; y */ SELECT $$c;d$$ AS v;"
    assert sqlcheck.split_statements(sql) == ["SELECT 'a;b'", "SELECT 2", "SELECT $$c;d$$ AS v"]


def test_comments_and_strings_are_blanked_so_a_keyword_in_them_is_not_a_keyword():
    cleaned = sqlcheck.blank_literals("SELECT 'DISTINCT ON' -- DISTINCT ON\nFROM t /* LIMIT 1, 2 */")
    assert "DISTINCT" not in cleaned and "LIMIT" not in cleaned and "SELECT" in cleaned and "FROM t" in cleaned


def test_the_sql_is_taken_from_sql_blocks_or_from_the_whole_reply():
    assert sqlcheck.extract_sql("```sql\nSELECT 1;\n```\ntext\n```postgresql\nSELECT 2;\n```") == "SELECT 1;\n\nSELECT 2;"
    assert sqlcheck.extract_sql("SELECT 1 FROM t;") == "SELECT 1 FROM t;"
    assert sqlcheck.extract_sql("Non lo so.") is None


def test_postgres_idioms_are_translated_to_what_sqlite_runs():
    assert sqlcheck.to_sqlite("SELECT x::int FROM t WHERE n ILIKE 'a%'") == "SELECT CAST(x AS INTEGER) FROM t WHERE n LIKE 'a%'"
    assert sqlcheck.to_sqlite("SELECT STRING_AGG(name, ', ') FROM t") == "SELECT GROUP_CONCAT(name, ', ') FROM t"
    assert sqlcheck.to_sqlite("SELECT * FROM t FETCH FIRST 3 ROWS ONLY") == "SELECT * FROM t LIMIT 3"
    assert sqlcheck.to_sqlite("SELECT GREATEST(a, b), LEAST(a, b) FROM t") == "SELECT MAX(a, b), MIN(a, b) FROM t"


def test_create_table_is_parsed_into_columns_and_constraints():
    tables = sqlcheck.parse_create_tables(
        "CREATE TABLE IF NOT EXISTS public.orders (\n id BIGSERIAL PRIMARY KEY,\n customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,\n"
        " total NUMERIC(10, 2) CHECK (total >= 0),\n email VARCHAR(120) UNIQUE,\n created_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n UNIQUE (customer_id, created_at)\n);"
    )
    (orders,) = tables
    assert orders.name == "orders"
    cols = {c.name: c for c in orders.columns}
    assert cols["id"].pk and cols["id"].type == "bigserial"
    assert cols["customer_id"].not_null and cols["customer_id"].references == "customers"
    assert cols["total"].type == "numeric" and cols["total"].check
    assert cols["email"].unique and cols["email"].type == "varchar"
    assert cols["created_at"].not_null and cols["created_at"].type == "timestamptz"
    assert orders.table_unique == [["customer_id", "created_at"]]


def test_indexes_are_parsed_with_their_method():
    indexes = sqlcheck.parse_indexes("CREATE UNIQUE INDEX CONCURRENTLY idx_o ON orders USING btree (customer_id, created_at DESC);\nCREATE INDEX ON docs USING gin (body jsonb_path_ops);")
    assert (indexes[0].table, indexes[0].columns, indexes[0].method, indexes[0].unique) == ("orders", ["customer_id", "created_at"], "btree", True)
    assert (indexes[1].table, indexes[1].columns, indexes[1].method) == ("docs", ["body"], "gin")


# -- queries -------------------------------------------------------------------------------------------------


def test_a_query_that_returns_the_expected_rows_passes_in_any_order():
    v = run(AVG, fenced("SELECT dept, AVG(salary) FROM employees GROUP BY dept ORDER BY dept DESC"))
    assert v.status == "passed" and v.method == "sqlite"


def test_a_postgres_flavoured_query_runs_after_translation():
    v = run(AVG, fenced("SELECT dept, AVG(salary)::numeric AS media FROM employees GROUP BY dept;"))
    assert v.status == "passed"


def test_the_order_matters_when_the_question_asks_for_it():
    spec = {**AVG, "ordered": True}
    assert run(spec, fenced("SELECT dept, AVG(salary) FROM employees GROUP BY dept ORDER BY dept")).status == "passed"
    v = run(spec, fenced("SELECT dept, AVG(salary) FROM employees GROUP BY dept ORDER BY dept DESC"))
    assert v.status == "failed" and "order" in v.reason.render().lower()


def test_wrong_rows_say_what_came_back_and_what_was_expected():
    v = run(AVG, fenced("SELECT dept, MAX(salary) FROM employees GROUP BY dept"))
    assert (v.status, v.kind) == ("failed", "wrong")
    assert "eng" in v.reason.render() or any("eng" in c.reason.render() for c in v.checks)
    cols = run(AVG, fenced("SELECT dept FROM employees GROUP BY dept"))
    assert cols.status == "failed" and "column" in cols.reason.render().lower()


def test_an_sql_error_is_reported_with_the_database_s_message():
    v = run(AVG, fenced("SELECT dept, AVG(salary) FROM employes GROUP BY dept"))
    assert (v.status, v.kind) == ("failed", "wrong") and "employes" in v.reason.render()


def test_only_the_last_select_counts_and_the_replys_own_setup_is_ignored():
    sql = "CREATE TABLE employees (id INT);\nINSERT INTO employees VALUES (9);\nSELECT dept, AVG(salary) FROM employees GROUP BY dept;"
    assert run(AVG, fenced(sql)).status == "passed"


def test_a_query_may_not_touch_files_or_settings():
    spec = {**AVG}
    for sql in ("ATTACH DATABASE 'x.db' AS other; SELECT 1", "PRAGMA writable_schema = 1; SELECT 1"):
        v = run(spec, fenced(sql))
        assert v.status == "failed"


def test_requirements_on_the_shape_of_the_query_are_checked_after_the_result():
    spec = {**AVG, "must_have": ["(?i)\\bGROUP BY\\b", "(?i)\\bAVG\\s*\\("], "must_not": ["(?i)\\bHAVING\\b"]}
    assert run(spec, fenced("SELECT dept, AVG(salary) FROM employees GROUP BY dept")).status == "passed"
    v = run(spec, fenced("SELECT dept, AVG(salary) FROM employees GROUP BY dept HAVING COUNT(*) > 0"))
    assert v.status == "partial" and any(not c.ok and "HAVING" in c.reason.render() for c in v.checks)


def test_mysql_and_sql_server_idioms_are_called_out_for_a_postgres_question():
    v = run({"type": "sql", "mode": "features", "dialect": "postgres", "must_have": ["(?i)\\bSELECT\\b"]}, fenced("SELECT `name` FROM employees LIMIT 1, 2"))
    assert v.status != "passed"
    failing = " ".join(c.reason.render() for c in v.checks if not c.ok)
    assert "`" in failing or "backtick" in failing.lower()
    assert "LIMIT" in failing


def test_no_sql_in_the_reply_is_a_format_failure():
    v = run(AVG, "Non saprei come scrivere questa query.")
    assert (v.status, v.kind) == ("failed", "format")


# -- statements that change data ------------------------------------------------------------------------------


def test_statements_that_change_data_are_judged_by_what_the_table_holds_afterwards():
    spec = {"type": "sql", "mode": "dml", "setup": SETUP, "then": "SELECT id, salary FROM employees ORDER BY id", "expect": [[1, 120], [2, 100], [3, 99], [4, 104], [5, 70]], "ordered": True}
    assert run(spec, fenced("UPDATE employees SET salary = salary + 9 WHERE dept = 'ops';")).status == "passed"
    assert run(spec, fenced("UPDATE employees SET salary = salary + 9;")).status == "failed"


# -- structure: tables, indexes, Postgres-only syntax -------------------------------------------------------------------

DDL = {
    "type": "sql", "mode": "ddl",
    "tables": [{"name": "orders", "columns": {
        "id": {"type": ["serial", "bigserial", "integer", "bigint", "uuid"], "pk": True},
        "customer_id": {"type": ["integer", "bigint", "uuid"], "not_null": True, "references": "customers"},
        "total": {"type": ["numeric", "decimal"], "check": True},
    }}],
    "indexes": [{"table": "orders", "columns": ["customer_id"]}],
}
GOOD_DDL = """CREATE TABLE orders (
  id BIGSERIAL PRIMARY KEY,
  customer_id BIGINT NOT NULL REFERENCES customers (id),
  total NUMERIC(12, 2) CHECK (total >= 0)
);
CREATE INDEX idx_orders_customer ON orders (customer_id);"""


def test_a_schema_with_the_right_columns_constraints_and_index_passes():
    v = run(DDL, fenced(GOOD_DDL))
    assert v.status == "passed" and v.method == "structure"


def test_each_missing_piece_of_a_schema_is_its_own_failing_check():
    bad = GOOD_DDL.replace("NOT NULL ", "").replace(" CHECK (total >= 0)", "").replace("CREATE INDEX idx_orders_customer ON orders (customer_id);", "")
    v = run(DDL, fenced(bad))
    assert v.status == "partial"
    failing = " | ".join(c.reason.render() for c in v.checks if not c.ok)
    assert "customer_id" in failing and "NOT NULL" in failing
    assert "CHECK" in failing or "check" in failing
    assert "index" in failing.lower()


def test_a_wrong_column_type_is_named():
    v = run(DDL, fenced(GOOD_DDL.replace("NUMERIC(12, 2)", "TEXT")))
    assert any(not c.ok and "total" in c.reason.render() and "text" in c.reason.render() for c in v.checks)


def test_a_missing_table_fails_the_whole_question():
    v = run(DDL, fenced("CREATE TABLE ordini (id INT);"))
    assert v.status == "failed" and "orders" in v.reason.render()


def test_postgres_only_syntax_is_looked_for_in_the_text_not_in_comments_or_strings():
    spec = {"type": "sql", "mode": "features", "dialect": "postgres", "must_have": ["(?i)\\bDISTINCT\\s+ON\\s*\\("], "must_not": []}
    good = "SELECT DISTINCT ON (customer_id) customer_id, total FROM orders ORDER BY customer_id, total DESC;"
    assert run(spec, fenced(good)).status == "passed"
    sneaky = "SELECT customer_id, MAX(total) FROM orders GROUP BY customer_id; -- not DISTINCT ON (x)"
    assert run(spec, fenced(sneaky)).status != "passed"
    assert run(spec, fenced("SELECT 'DISTINCT ON (x)' FROM orders")).status != "passed"
