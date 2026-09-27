import json
from pathlib import Path

import pytest

from analyst.guardrails import Guardrails

ROOT = Path(__file__).resolve().parents[1]
GUARD = Guardrails(json.loads((ROOT / "catalog/semantic_catalog.json").read_text()))

ALLOWED = [
    "select brand, sum(net_revenue) from metrics.kpi_location_monthly group by 1",
    "with t as (select * from certified.fct_payments) select count(*) from t",
    "select l.location_name, sum(p.amount) from certified.fct_payments p "
    "join certified.dim_location l on l.location_id = p.location_id group by 1",
    "select channel, avg(case when is_converted then 1 else 0 end) from certified.fct_leads group by 1",
    "select location_id from certified.dim_location union select location_id from metrics.kpi_location_monthly",
]

BLOCKED = {
    "raw table": "select * from raw.crm_payments",
    "staging table": "select * from staging.stg_crm__customers",
    "unqualified table": "select * from fct_payments",
    "system catalog": "select * from information_schema.tables",
    "delete": "delete from certified.fct_payments",
    "drop": "drop table certified.fct_payments",
    "create": "create table certified.x as select 1",
    "update": "update certified.dim_location set brand = 'x'",
    "two statements": "select 1; drop table certified.fct_payments",
    "file read in from": "select * from read_csv('/etc/passwd')",
    "file read in select": "select read_csv('/etc/passwd')",
    "attach": "attach 'other.db' as other",
    "copy out": "copy certified.fct_payments to 'out.csv'",
    "pragma": "pragma database_list",
    "set": "set memory_limit = '1GB'",
    "env var": "select getenv('HOME')",
    "system table fn": "select * from duckdb_tables()",
    "catalog prefix": "select * from src.certified.fct_payments",
    "raw via subquery": "select * from certified.fct_payments where customer_id in (select customer_id from raw.crm_customers)",
    "raw via union": "select location_id from certified.dim_location union select location_id from raw.crm_locations",
    "raw via cte": "with x as (select * from raw.crm_customers) select * from x",
    "made-up column": "select c.email from certified.dim_customer c",
    "empty": "   ",
    "garbage": "selec * form nowhere",
}


@pytest.mark.parametrize("sql", ALLOWED)
def test_certified_reads_are_allowed_and_capped(sql):
    v = GUARD.check_sql(sql)
    assert v.allowed, v.reason
    assert v.sql.endswith(f"LIMIT {GUARD.max_rows}")
    assert v.tables and all(t.split(".")[0] in {"certified", "metrics"} for t in v.tables)


@pytest.mark.parametrize("name,sql", BLOCKED.items(), ids=list(BLOCKED))
def test_everything_else_is_blocked_with_a_reason(name, sql):
    v = GUARD.check_sql(sql)
    assert not v.allowed
    assert v.reason


def test_catalog_contains_no_personal_data_columns():
    catalog = json.loads((ROOT / "catalog/semantic_catalog.json").read_text())
    columns = {c["name"].lower() for t in catalog["tables"] for c in t["columns"]}
    assert not columns & {"email", "email_normalized", "first_name", "last_name", "phone", "address"}
