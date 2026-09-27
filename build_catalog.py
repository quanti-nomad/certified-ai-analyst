"""Build the analyst's data and semantic catalog from the data platform.

Reads a built copy of the multi-location-data-platform repo and produces:

  data/certified_demo.duckdb       ONLY the certified and metrics schemas
  catalog/semantic_catalog.json    certified tables, columns, descriptions,
                                   tiers and the KPI Bible

Raw and staging data are never copied, so the analyst cannot reach them even
by mistake. The committed demo files let this repo run on its own; rebuild
them with:

    python build_catalog.py --platform ../multi-location-data-platform
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent
ALLOWED_SCHEMAS = ("certified", "metrics")


def build(platform: Path, out_db: Path, out_catalog: Path) -> dict:
    manifest = json.loads((platform / "transform/target/manifest.json").read_text())
    bible = yaml.safe_load((platform / "kpis/kpi_bible.yml").read_text())
    src_db = platform / "data/warehouse.duckdb"

    certified = {
        n["name"]: n
        for n in manifest["nodes"].values()
        if n["resource_type"] == "model"
        and n["schema"] in ALLOWED_SCHEMAS
        and n["config"].get("meta", {}).get("certified")
    }

    out_db.parent.mkdir(parents=True, exist_ok=True)
    out_db.unlink(missing_ok=True)
    con = duckdb.connect(str(out_db))
    con.execute(f"ATTACH '{src_db}' AS src (READ_ONLY)")
    tables = []
    for name, node in sorted(certified.items()):
        schema = node["schema"]
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        con.execute(f"CREATE TABLE {schema}.{name} AS SELECT * FROM src.{schema}.{name}")
        cols = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_catalog = current_database() AND table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position",
            [schema, name],
        ).fetchall()
        documented = node.get("columns", {})
        rows = con.execute(f"SELECT COUNT(*) FROM {schema}.{name}").fetchone()[0]
        tables.append({
            "name": f"{schema}.{name}",
            "description": node.get("description", "").strip(),
            "tier": node["config"]["meta"].get("tier"),
            "owner": node["config"]["meta"].get("owner"),
            "rows": rows,
            "columns": [
                {"name": c, "type": t, "description": documented.get(c, {}).get("description", "")}
                for c, t in cols
            ],
        })
    con.execute("DETACH src")
    con.close()

    kpis = [
        {k: kpi[k] for k in ("id", "name", "definition", "formula", "grain", "tier", "unit")}
        | {"source": f"metrics.{kpi['source_model']}.{kpi['column']}"}
        for kpi in bible["kpis"]
    ]
    catalog = {
        "description": (
            "Certified data for a fictional two-brand clinic chain (synthetic). "
            "Vitality Clinics locations are corporate-owned; Lumen Wellness locations are franchised."
        ),
        "tables": tables,
        "kpis": kpis,
    }
    out_catalog.parent.mkdir(parents=True, exist_ok=True)
    out_catalog.write_text(json.dumps(catalog, indent=2, default=str))
    return {"tables": len(tables), "kpis": len(kpis)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", type=Path, default=ROOT.parent / "multi-location-data-platform")
    ap.add_argument("--db", type=Path, default=ROOT / "data/certified_demo.duckdb")
    ap.add_argument("--catalog", type=Path, default=ROOT / "catalog/semantic_catalog.json")
    a = ap.parse_args()
    print(build(a.platform, a.db, a.catalog))
