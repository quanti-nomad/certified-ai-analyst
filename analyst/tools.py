"""The only three things the model can do: read KPI definitions, read table
documentation, and run a guarded, read-only query. Every call is recorded."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from .guardrails import Guardrails

TOOL_SPECS = [
    {
        "name": "list_kpis",
        "description": (
            "Return the KPI Bible: the official definition, formula, grain, tier and source column "
            "of every certified KPI. Call this first for any question about a business metric."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "describe_tables",
        "description": (
            "Return the certified tables with their descriptions, certification tier, row counts and "
            "columns. These are the ONLY tables that can be queried."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tables": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional list of table names (e.g. metrics.kpi_location_monthly). Omit for all.",
                }
            },
        },
    },
    {
        "name": "run_query",
        "description": (
            "Run ONE read-only DuckDB SELECT against certified tables (schema-qualified, e.g. "
            "certified.fct_payments). Results are capped at 200 rows. Queries that touch anything "
            "uncertified, write data or call system functions are rejected with a reason."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT statement."},
                "purpose": {"type": "string", "description": "One line on what this query answers."},
            },
            "required": ["sql", "purpose"],
        },
    },
]


@dataclass
class ToolCall:
    tool: str
    input: dict
    ok: bool
    summary: str
    sql_executed: str | None = None
    tables: tuple = ()
    rows_returned: int = 0
    ms: int = 0


@dataclass
class Toolbox:
    catalog: dict
    db_path: Path
    audit: list[ToolCall] = field(default_factory=list)

    def __post_init__(self):
        self.guard = Guardrails(self.catalog)

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """Execute a tool call. Returns (content for the model, is_error)."""
        start = time.time()
        if name == "list_kpis":
            content, ok, extra = json.dumps(self.catalog["kpis"], indent=1), True, {}
            summary = f"{len(self.catalog['kpis'])} KPI definitions"
        elif name == "describe_tables":
            wanted = {t.lower() for t in (args.get("tables") or [])}
            tables = [t for t in self.catalog["tables"] if not wanted or t["name"].lower() in wanted]
            content, ok, extra = json.dumps(tables, indent=1), True, {}
            summary = f"{len(tables)} table(s) described"
        elif name == "run_query":
            content, ok, summary, extra = self._query(args.get("sql", ""))
        else:
            content, ok, summary, extra = f"Unknown tool '{name}'.", False, "unknown tool", {}
        self.audit.append(ToolCall(name, args, ok, summary, ms=int((time.time() - start) * 1000), **extra))
        return content, not ok

    def _query(self, sql: str):
        verdict = self.guard.check_sql(sql)
        if not verdict.allowed:
            return f"BLOCKED: {verdict.reason}", False, f"blocked: {verdict.reason}", {}
        # Defense in depth: even if a guardrail were bypassed, this connection
        # cannot write, and cannot read files or reach the network.
        con = duckdb.connect(str(self.db_path), read_only=True,
                             config={"enable_external_access": False})
        try:
            cur = con.execute(verdict.sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        except duckdb.Error as e:
            return f"QUERY ERROR: {str(e).splitlines()[0]}", False, "query error", {"sql_executed": verdict.sql}
        finally:
            con.close()
        payload = {"columns": cols, "rows": [list(r) for r in rows], "row_count": len(rows),
                   "truncated": len(rows) >= self.guard.max_rows, "tables": list(verdict.tables)}
        extra = {"sql_executed": verdict.sql, "tables": verdict.tables, "rows_returned": len(rows)}
        return json.dumps(payload, default=str), True, f"{len(rows)} row(s)", extra
