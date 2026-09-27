"""Guardrails between the language model and the data.

Every SQL statement the model writes passes through `check_sql` before it runs.
A statement is allowed only if ALL of these hold:

1. it parses, and is exactly one statement
2. it is a read-only query (SELECT / WITH ... SELECT / UNION of selects)
3. every table it reads is a certified table listed in the semantic catalog
   (CTE names defined inside the query are fine)
4. it calls no file, network or system functions (read_csv, attach, ...)
5. every column it references by table alias exists in the catalog

Allowed queries are wrapped with a row cap. The database connection is also
opened read-only, so even a guardrail bug could not change data. These are
layered defenses, not a single check.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

MAX_ROWS = 200

BLOCKED_FUNCTIONS = {
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
    "read_blob", "glob", "sqlite_scan", "postgres_scan", "mysql_scan", "parquet_scan",
    "httpfs", "getenv", "current_setting", "duckdb_settings", "duckdb_tables",
    "duckdb_columns", "duckdb_databases", "pragma", "query", "query_table",
}
WRITE_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Merge,
    exp.Command, exp.Copy, exp.Attach, exp.Detach, exp.Set, exp.Pragma, exp.Use,
    exp.TruncateTable, exp.Transaction, exp.Commit, exp.Rollback,
)


@dataclass
class Verdict:
    allowed: bool
    sql: str = ""          # the capped SQL to run, when allowed
    reason: str = ""       # why it was blocked, written for the model to act on
    tables: tuple = ()     # certified tables the query reads


class Guardrails:
    def __init__(self, catalog: dict, max_rows: int = MAX_ROWS):
        self.max_rows = max_rows
        self.columns = {
            t["name"].lower(): {c["name"].lower() for c in t["columns"]} for t in catalog["tables"]
        }

    def check_sql(self, sql: str) -> Verdict:
        sql = (sql or "").strip().rstrip(";")
        if not sql:
            return Verdict(False, reason="Empty query.")
        try:
            statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
        except sqlglot.errors.ParseError as e:
            return Verdict(False, reason=f"Query could not be parsed: {str(e).splitlines()[0]}")
        if len(statements) != 1:
            return Verdict(False, reason="Send exactly one statement per query.")
        tree = statements[0]

        if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)) or any(
            isinstance(node, WRITE_NODES) for node in tree.walk()
        ):
            return Verdict(False, reason="Only read-only SELECT queries are allowed.")

        for fn in tree.find_all(exp.Func):
            name = (fn.sql_name() if not isinstance(fn, exp.Anonymous) else fn.name).lower()
            if name in BLOCKED_FUNCTIONS:
                return Verdict(False, reason=f"Function '{name}' is not allowed.")

        cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        alias_to_table: dict[str, str] = {}
        tables = set()
        for t in tree.find_all(exp.Table):
            if isinstance(t.this, exp.Func) or t.args.get("catalog"):
                return Verdict(False, reason="Table functions and database prefixes are not allowed.")
            name = t.name.lower()
            schema = (t.db or "").lower()
            if not schema and name in cte_names:
                continue
            full = f"{schema}.{name}" if schema else name
            if full not in self.columns:
                allowed = ", ".join(sorted(self.columns))
                return Verdict(
                    False,
                    reason=f"Table '{full}' is not certified. Use only: {allowed}. "
                    "If the question needs data outside these tables, say so instead of guessing.",
                )
            tables.add(full)
            alias_to_table[(t.alias or name).lower()] = full

        for col in tree.find_all(exp.Column):
            qualifier = col.table.lower()
            if qualifier and qualifier in alias_to_table:
                table = alias_to_table[qualifier]
                if col.name.lower() not in self.columns[table]:
                    return Verdict(False, reason=f"Column '{col.name}' does not exist on {table}.")

        capped = f"SELECT * FROM ({tree.sql(dialect='duckdb')}) AS certified_result LIMIT {self.max_rows}"
        return Verdict(True, sql=capped, tables=tuple(sorted(tables)))
