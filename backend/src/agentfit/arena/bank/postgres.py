"""PostgreSQL: queries run on fixed tables and compared row by row, a schema read for its columns and constraints, and the
features only Postgres has (DISTINCT ON, upserts, JSONB and GIN, SKIP LOCKED) looked for in the text.

The expected rows of a query are not typed in: they are what the reference query returns on the same data, so the
question and its answer cannot disagree. The data are small enough to check by eye.
"""

from __future__ import annotations

from agentfit.arena import sqlcheck
from agentfit.arena.bank.kit import make
from agentfit.arena.model import Question

SETUP = """
CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT NOT NULL, dept_id INTEGER REFERENCES departments(id),
                        manager_id INTEGER REFERENCES employees(id), salary INTEGER NOT NULL, hired_year INTEGER NOT NULL);
INSERT INTO departments VALUES (1, 'Ingegneria'), (2, 'Vendite'), (3, 'Risorse umane'), (4, 'Legale');
INSERT INTO employees VALUES (1, 'Ada', 1, NULL, 5200, 2015), (2, 'Bruno', 1, 1, 4300, 2018), (3, 'Carla', 1, 1, 4800, 2019),
                             (4, 'Dario', 2, NULL, 3900, 2016), (5, 'Elisa', 2, 4, 3100, 2020), (6, 'Fabio', 2, 4, 3100, 2021),
                             (7, 'Giada', 3, NULL, 3500, 2017), (8, 'Hugo', 3, 7, 2800, 2022);
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), month INTEGER NOT NULL, total INTEGER NOT NULL, status TEXT NOT NULL);
INSERT INTO customers VALUES (1, 'Rossi Srl', 'Milano'), (2, 'Bianchi SpA', 'Torino'), (3, 'Verdi & C', 'Milano'), (4, 'Neri Snc', 'Roma'), (5, 'Gallo Srl', NULL);
INSERT INTO orders VALUES (1, 1, 1, 1200, 'paid'), (2, 1, 2, 800, 'paid'), (3, 2, 1, 500, 'refunded'), (4, 2, 3, 1500, 'paid'),
                          (5, 3, 2, 300, 'paid'), (6, 1, 3, 950, 'pending'), (7, 3, 3, 700, 'paid'), (8, 2, 2, 2200, 'paid');
"""

SCHEMA = ("Tabelle: departments(id, name); employees(id, name, dept_id, manager_id, salary, hired_year); "
          "customers(id, name, city); orders(id, customer_id, month, total, status).")
_REPLY = "Rispondi con il solo SQL, in un blocco ```sql."


def sql(text: str) -> str:
    return f"```sql\n{text.strip()}\n```"


def _rows(reference: str) -> list[list]:
    return [list(r) for r in sqlcheck.run_on_sqlite(SETUP, [reference])]


def _query(n: int, title: str, ask: str, reference: str, wrong: list[str], *, ordered: bool = False, must_have: list[str] | None = None, difficulty: int = 2) -> Question:
    expect = _rows(reference)
    check = {"type": "sql", "mode": "query", "setup": SETUP, "expect": expect, "ordered": ordered, "dialect": "postgres", "must_have": must_have or []}
    return make("sql", n, title, f"{SCHEMA}\n\n{ask}\n{_REPLY}", check, f"{len(expect)} righe, per esempio {expect[:2]}", sql(reference), wrong=[sql(w) for w in wrong], max_tokens=800, difficulty=difficulty)


def _features(n: int, title: str, ask: str, reference: str, wrong: list[str], must_have: list[str], labels: dict[str, str], *, literals: bool = False, difficulty: int = 3) -> Question:
    check = {"type": "sql", "mode": "features", "dialect": "postgres", "must_have": must_have, "must_not": [], "labels": labels, "literals": literals}
    return make("sql", n, title, f"{ask}\n{_REPLY}", check, "; ".join(labels.values()), sql(reference), wrong=[sql(w) for w in wrong], max_tokens=900, difficulty=difficulty)


def build(seed: int) -> list[Question]:
    out: list[Question] = []

    out.append(_query(1, "Stipendio medio per reparto", "Per ogni reparto che ha dipendenti, restituisci il nome del reparto e lo stipendio medio dei suoi dipendenti, arrotondato a due decimali.",
                      "SELECT d.name, ROUND(AVG(e.salary), 2) FROM employees e JOIN departments d ON d.id = e.dept_id GROUP BY d.name",
                      ["SELECT d.name, AVG(e.salary) FROM employees e JOIN departments d ON d.id = e.dept_id GROUP BY d.name", "SELECT d.name, ROUND(AVG(e.salary), 2) FROM departments d LEFT JOIN employees e ON d.id = e.dept_id GROUP BY d.name",
                       "SELECT dept_id, ROUND(AVG(salary), 2) FROM employees GROUP BY dept_id", "SELECT name, salary FROM employees"], difficulty=1))

    out.append(_query(2, "Clienti senza ordini", "Elenca i nomi dei clienti che non hanno mai fatto un ordine.",
                      "SELECT c.name FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)",
                      ["SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.id", "SELECT name FROM customers WHERE id NOT IN (SELECT id FROM orders)", "SELECT DISTINCT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id"], difficulty=1))

    out.append(_query(3, "Il più pagato di ogni reparto", "Per ogni reparto, restituisci il nome del reparto e il nome del dipendente con lo stipendio più alto (un solo dipendente per reparto).",
                      "SELECT d.name, e.name FROM employees e JOIN departments d ON d.id = e.dept_id WHERE e.salary = (SELECT MAX(salary) FROM employees x WHERE x.dept_id = e.dept_id)",
                      ["SELECT d.name, e.name FROM employees e JOIN departments d ON d.id = e.dept_id ORDER BY e.salary DESC LIMIT 1", "SELECT d.name, MAX(e.salary) FROM employees e JOIN departments d ON d.id = e.dept_id GROUP BY d.name",
                       "SELECT d.name, e.name FROM employees e JOIN departments d ON d.id = e.dept_id"], difficulty=2))

    out.append(_query(4, "Fatturato mensile", "Per ogni mese restituisci il mese e il totale degli ordini pagati (status = 'paid'), ordinando per mese.",
                      "SELECT month, SUM(total) FROM orders WHERE status = 'paid' GROUP BY month ORDER BY month",
                      ["SELECT month, SUM(total) FROM orders GROUP BY month ORDER BY month", "SELECT month, COUNT(*) FROM orders WHERE status = 'paid' GROUP BY month ORDER BY month",
                       "SELECT month, SUM(total) FROM orders WHERE status = 'paid' GROUP BY month ORDER BY month DESC"], ordered=True, difficulty=1))

    out.append(_query(5, "Sopra la media del reparto", "Restituisci nome e stipendio dei dipendenti che guadagnano più della media del proprio reparto.",
                      "SELECT e.name, e.salary FROM employees e WHERE e.salary > (SELECT AVG(x.salary) FROM employees x WHERE x.dept_id = e.dept_id)",
                      ["SELECT name, salary FROM employees WHERE salary > (SELECT AVG(salary) FROM employees)", "SELECT name, salary FROM employees WHERE salary > 3500",
                       "SELECT name, AVG(salary) FROM employees GROUP BY name"], difficulty=2))

    raise_ref = "UPDATE employees SET salary = salary + 200 WHERE hired_year < 2019 AND dept_id = (SELECT id FROM departments WHERE name = 'Ingegneria');"
    raise_check = {"type": "sql", "mode": "dml", "setup": SETUP, "then": "SELECT id, salary FROM employees ORDER BY id", "ordered": True, "dialect": "postgres",
                   "expect": [list(r) for r in sqlcheck.run_on_sqlite(SETUP, [raise_ref.rstrip(";")], "SELECT id, salary FROM employees ORDER BY id")]}
    out.append(make("sql", 6, "Aumento mirato", f"{SCHEMA}\n\nAumenta di 200 euro lo stipendio dei dipendenti del reparto «Ingegneria» assunti prima del 2019.\n{_REPLY}", raise_check, "Ada e Bruno +200, gli altri invariati",
                    sql(raise_ref), wrong=[sql("UPDATE employees SET salary = salary + 200 WHERE hired_year < 2019;"), sql("UPDATE employees SET salary = salary + 200 WHERE dept_id = 1;"),
                                           sql("UPDATE employees SET salary = 200 WHERE hired_year < 2019 AND dept_id = 1;")], max_tokens=800, difficulty=2))

    ddl_check = {
        "type": "sql", "mode": "ddl", "dialect": "postgres",
        "tables": [{"name": "invoices", "columns": {
            "id": {"type": ["serial", "bigserial", "integer", "bigint", "smallserial", "uuid"], "pk": True},
            "customer_id": {"type": ["integer", "bigint"], "not_null": True, "references": "customers"},
            "issued_on": {"type": ["date", "timestamp", "timestamptz"], "not_null": True},
            "amount": {"type": ["numeric", "decimal"], "check": True},
            "status": {"type": ["text", "varchar"], "not_null": True, "check": True},
        }}],
        "indexes": [{"table": "invoices", "columns": ["customer_id"]}],
    }
    ddl_ref = ("CREATE TABLE invoices (\n  id BIGSERIAL PRIMARY KEY,\n  customer_id INTEGER NOT NULL REFERENCES customers (id),\n  issued_on DATE NOT NULL,\n  amount NUMERIC(12, 2) NOT NULL CHECK (amount >= 0),\n"
               "  status TEXT NOT NULL CHECK (status IN ('draft', 'sent', 'paid'))\n);\nCREATE INDEX idx_invoices_customer_id ON invoices (customer_id);")
    out.append(make("sql", 7, "Tabella delle fatture", "Scrivi il `CREATE TABLE` PostgreSQL di una tabella `invoices`: `id` chiave primaria a incremento automatico; `customer_id` intero obbligatorio che fa riferimento a `customers(id)`; "
                    "`issued_on` data obbligatoria; `amount` numero con due decimali, mai negativo; `status` testo obbligatorio che può valere solo 'draft', 'sent' o 'paid'. "
                    f"Aggiungi anche l'indice su `customer_id`.\n{_REPLY}", ddl_check, "tabella invoices con chiave, riferimento, vincoli e indice su customer_id", sql(ddl_ref),
                    wrong=[sql(ddl_ref.replace("NUMERIC(12, 2) NOT NULL CHECK (amount >= 0)", "FLOAT").replace("CREATE INDEX idx_invoices_customer_id ON invoices (customer_id);", "")),
                           sql("CREATE TABLE invoices (id INT AUTO_INCREMENT PRIMARY KEY, customer_id INT, issued_on DATE, amount DECIMAL(12,2), status VARCHAR(10));"),
                           sql("CREATE TABLE fatture (id SERIAL PRIMARY KEY);")], max_tokens=900, difficulty=2))

    out.append(_query(8, "Fatturato cumulato", "Per ogni mese restituisci il mese, il totale degli ordini pagati (status = 'paid') e il totale cumulato dei mesi fino a quello incluso. Colonne: mese, totale, cumulato; ordina per mese.",
                      "SELECT month, SUM(total) AS totale, SUM(SUM(total)) OVER (ORDER BY month) AS cumulato FROM orders WHERE status = 'paid' GROUP BY month ORDER BY month",
                      ["SELECT month, SUM(total), SUM(total) FROM orders WHERE status = 'paid' GROUP BY month ORDER BY month", "SELECT month, SUM(total), SUM(SUM(total)) OVER () FROM orders WHERE status = 'paid' GROUP BY month ORDER BY month",
                       "SELECT month, total, SUM(total) OVER (ORDER BY month) FROM orders WHERE status = 'paid' ORDER BY month"], ordered=True, must_have=["(?i)\\bOVER\\s*\\("], difficulty=3))

    out.append(_features(9, "DISTINCT ON", "Scrivi una query PostgreSQL che restituisca, per ogni cliente, il suo ordine di importo più alto (colonne customer_id, id, total) usando `DISTINCT ON`.",
                         "SELECT DISTINCT ON (customer_id) customer_id, id, total FROM orders ORDER BY customer_id, total DESC;",
                         ["SELECT customer_id, MAX(total) FROM orders GROUP BY customer_id;", "SELECT DISTINCT ON (customer_id) customer_id, id, total FROM orders;", "SELECT DISTINCT customer_id, id, total FROM orders ORDER BY total DESC;"],
                         ["(?is)\\bDISTINCT\\s+ON\\s*\\(\\s*customer_id\\s*\\)", "(?is)\\bDISTINCT\\s+ON\\s*\\(\\s*customer_id\\s*\\).*\\bORDER\\s+BY\\s+customer_id\\b.*\\btotal\\s+DESC\\b"],
                         {"(?is)\\bDISTINCT\\s+ON\\s*\\(\\s*customer_id\\s*\\)": "DISTINCT ON (customer_id)", "(?is)\\bDISTINCT\\s+ON\\s*\\(\\s*customer_id\\s*\\).*\\bORDER\\s+BY\\s+customer_id\\b.*\\btotal\\s+DESC\\b": "ORDER BY customer_id, total DESC"}))

    out.append(_features(10, "Upsert con RETURNING", "Hai la tabella `stock(sku TEXT PRIMARY KEY, qty INTEGER)`. Scrivi un `INSERT` PostgreSQL che inserisce lo sku 'A1' con quantità 5 e, se lo sku esiste già, somma 5 alla quantità esistente; "
                         "il comando deve restituire la riga risultante.",
                         "INSERT INTO stock (sku, qty) VALUES ('A1', 5)\nON CONFLICT (sku) DO UPDATE SET qty = stock.qty + EXCLUDED.qty\nRETURNING sku, qty;",
                         ["INSERT INTO stock (sku, qty) VALUES ('A1', 5) ON DUPLICATE KEY UPDATE qty = qty + 5;", "INSERT INTO stock (sku, qty) VALUES ('A1', 5) ON CONFLICT (sku) DO NOTHING RETURNING sku, qty;",
                          "INSERT INTO stock (sku, qty) VALUES ('A1', 5) ON CONFLICT (sku) DO UPDATE SET qty = 5;"],
                         ["(?is)\\bINSERT\\s+INTO\\s+stock\\b", "(?is)\\bON\\s+CONFLICT\\s*\\(\\s*sku\\s*\\)\\s*DO\\s+UPDATE\\b", "(?is)\\bqty\\s*=\\s*(?:stock\\.)?qty\\s*\\+\\s*(?:EXCLUDED\\.qty|5)\\b", "(?is)\\bRETURNING\\b"],
                         {"(?is)\\bINSERT\\s+INTO\\s+stock\\b": "INSERT INTO stock", "(?is)\\bON\\s+CONFLICT\\s*\\(\\s*sku\\s*\\)\\s*DO\\s+UPDATE\\b": "ON CONFLICT (sku) DO UPDATE",
                          "(?is)\\bqty\\s*=\\s*(?:stock\\.)?qty\\s*\\+\\s*(?:EXCLUDED\\.qty|5)\\b": "qty = stock.qty + …", "(?is)\\bRETURNING\\b": "RETURNING"}))

    out.append(_features(11, "JSONB e indice GIN", "Hai la tabella `events(id bigserial primary key, payload jsonb)`. Scrivi (1) una query che restituisca gli `id` degli eventi il cui payload ha il campo `type` uguale a 'login', usando l'operatore `->>`; "
                         "(2) l'indice PostgreSQL adatto a velocizzare le ricerche di contenimento (`@>`) sul payload.",
                         "SELECT id FROM events WHERE payload ->> 'type' = 'login';\nCREATE INDEX idx_events_payload ON events USING gin (payload);",
                         ["SELECT id FROM events WHERE JSON_EXTRACT(payload, '$.type') = 'login';", "SELECT id FROM events WHERE payload ->> 'type' = 'login';\nCREATE INDEX idx_events_payload ON events (payload);",
                          "SELECT id FROM events WHERE payload -> 'type' = 'login';\nCREATE INDEX idx_events_payload ON events USING gin (payload);"],
                         ["(?is)\\bpayload\\s*->>\\s*'type'\\s*=\\s*'login'", "(?is)\\bCREATE\\s+INDEX\\b[^;]*\\bON\\s+events\\s+USING\\s+gin\\s*\\(\\s*payload\\b"],
                         {"(?is)\\bpayload\\s*->>\\s*'type'\\s*=\\s*'login'": "payload ->> 'type' = 'login'", "(?is)\\bCREATE\\s+INDEX\\b[^;]*\\bON\\s+events\\s+USING\\s+gin\\s*\\(\\s*payload\\b": "USING gin (payload)"}, literals=True))

    out.append(_features(12, "Coda di lavori con SKIP LOCKED", "Hai la tabella `jobs(id bigserial primary key, status text, priority int, payload jsonb)`. Scrivi la query PostgreSQL con cui un worker prende e blocca fino a 5 job in stato 'queued', "
                         "dai più prioritari (priority più alta) ai meno, saltando quelli già bloccati da altre transazioni.",
                         "SELECT id, payload FROM jobs WHERE status = 'queued' ORDER BY priority DESC LIMIT 5 FOR UPDATE SKIP LOCKED;",
                         ["SELECT id, payload FROM jobs WHERE status = 'queued' ORDER BY priority DESC LIMIT 5;", "SELECT id, payload FROM jobs WHERE status = 'queued' ORDER BY priority DESC LIMIT 5 FOR UPDATE;",
                          "SELECT TOP 5 id, payload FROM jobs WHERE status = 'queued' ORDER BY priority DESC FOR UPDATE SKIP LOCKED;"],
                         ["(?is)\\bFOR\\s+UPDATE\\s+SKIP\\s+LOCKED\\b", "(?is)\\bORDER\\s+BY\\s+priority\\s+DESC\\b", "(?is)\\bLIMIT\\s+5\\b"],
                         {"(?is)\\bFOR\\s+UPDATE\\s+SKIP\\s+LOCKED\\b": "FOR UPDATE SKIP LOCKED", "(?is)\\bORDER\\s+BY\\s+priority\\s+DESC\\b": "ORDER BY priority DESC", "(?is)\\bLIMIT\\s+5\\b": "LIMIT 5"}))
    return out
