import json
from pathlib import Path

import duckdb
import pytest

from analyst.tools import Toolbox

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "catalog/semantic_catalog.json").read_text())
DB = ROOT / "data/certified_demo.duckdb"


def test_query_returns_rows_and_is_audited():
    tb = Toolbox(CATALOG, DB)
    content, is_error = tb.run("run_query", {"sql": "select count(*) as n from certified.dim_location", "purpose": "t"})
    assert not is_error
    assert json.loads(content)["rows"] == [[24]]
    call = tb.audit[-1]
    assert call.ok and call.tables == ("certified.dim_location",) and call.rows_returned == 1


def test_blocked_query_is_reported_to_model_and_audited():
    tb = Toolbox(CATALOG, DB)
    content, is_error = tb.run("run_query", {"sql": "select * from raw.crm_payments", "purpose": "t"})
    assert is_error and content.startswith("BLOCKED")
    assert not tb.audit[-1].ok and tb.audit[-1].sql_executed is None


def test_rows_are_capped():
    tb = Toolbox(CATALOG, DB)
    content, _ = tb.run("run_query", {"sql": "select * from certified.fct_payments", "purpose": "t"})
    payload = json.loads(content)
    assert payload["row_count"] == 200 and payload["truncated"]


def test_database_is_locked_down_even_without_guardrails():
    con = duckdb.connect(str(DB), read_only=True, config={"enable_external_access": False})
    with pytest.raises(duckdb.Error):
        con.execute("select * from read_csv('/etc/hosts')")
    with pytest.raises(duckdb.Error):
        con.execute("create table certified.x as select 1")
    con.close()


def test_kpi_and_table_docs():
    tb = Toolbox(CATALOG, DB)
    kpis = json.loads(tb.run("list_kpis", {})[0])
    assert {k["id"] for k in kpis} >= {"net_revenue", "member_churn_rate"}
    tables = json.loads(tb.run("describe_tables", {"tables": ["metrics.kpi_location_monthly"]})[0])
    assert len(tables) == 1 and tables[0]["tier"] == "board"
