"""The agent loop, driven by a scripted stand-in for the Claude API."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

from analyst.agent import CertifiedAnalyst

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "catalog/semantic_catalog.json").read_text())
DB = ROOT / "data/certified_demo.duckdb"


def tool(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def text(t):
    return NS(type="text", text=t)


class ScriptedClient:
    """Returns pre-written responses in order and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs["messages"], default=lambda o: o.__dict__)))
        content = self.responses.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return NS(content=content, stop_reason=stop)


def test_answers_from_certified_kpis_with_audit_trail(tmp_path):
    client = ScriptedClient(
        [tool("t1", "list_kpis")],
        [tool("t2", "run_query", purpose="June active members",
              sql="select sum(active_members) as m from metrics.kpi_location_monthly "
                  "where brand = 'Lumen Wellness' and month_start = date '2026-06-01'")],
        [text("Lumen Wellness had 1,207 active members in June 2026.")],
    )
    log = tmp_path / "audit.jsonl"
    result = CertifiedAnalyst(client, CATALOG, DB, audit_log=log).ask("Lumen members in June 2026?")

    assert "1,207" in result.answer
    assert result.tables_used == ["metrics.kpi_location_monthly"]
    assert [c.tool for c in result.tool_calls] == ["list_kpis", "run_query"]
    # the real query result was what the model saw
    tool_result = client.requests[2][-1]["content"][0]
    assert json.loads(tool_result["content"])["rows"] == [[1207]]
    assert json.loads(log.read_text().splitlines()[0])["tables_used"] == ["metrics.kpi_location_monthly"]


def test_blocked_query_is_returned_as_error_and_model_recovers():
    client = ScriptedClient(
        [tool("t1", "run_query", purpose="try raw", sql="select sum(amount) from raw.crm_payments")],
        [tool("t2", "run_query", purpose="certified", sql="select sum(amount) from certified.fct_payments")],
        [text("Net revenue is in the certified payments ledger.")],
    )
    result = CertifiedAnalyst(client, CATALOG, DB).ask("total revenue from raw data?")

    first_result = client.requests[1][-1]["content"][0]
    assert first_result["is_error"] and "not certified" in first_result["content"]
    assert len(result.blocked) == 1 and len(result.queries) == 1
    assert result.tables_used == ["certified.fct_payments"]


def test_stops_after_max_turns():
    looping = [[tool(f"t{i}", "list_kpis")] for i in range(5)]
    result = CertifiedAnalyst(ScriptedClient(*looping), CATALOG, DB, max_turns=3).ask("loop")
    assert result.turns == 3 and result.stopped_reason == "max_turns"
    assert "could not finish" in result.answer
