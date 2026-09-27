"""Ask the certified analyst a question from the command line.

    export ANTHROPIC_API_KEY=...        # from console.anthropic.com
    python ask.py "Which five locations had the highest member churn in Q2 2026?"
    python ask.py --show-sql "How did net revenue trend by brand over the last 6 months?"

Every question, tool call, query and answer is appended to logs/audit.jsonl.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from analyst.agent import DEFAULT_MODEL, CertifiedAnalyst, load_catalog

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--model", default=os.environ.get("ANALYST_MODEL", DEFAULT_MODEL))
    ap.add_argument("--show-sql", action="store_true", help="print every query that ran")
    a = ap.parse_args()

    import anthropic  # imported here so the tests never need the SDK configured

    analyst = CertifiedAnalyst(
        anthropic.Anthropic(),
        load_catalog(ROOT / "catalog/semantic_catalog.json"),
        ROOT / "data/certified_demo.duckdb",
        model=a.model,
        audit_log=ROOT / "logs/audit.jsonl",
    )
    result = analyst.ask(a.question)

    print("\n" + result.answer + "\n")
    print("-" * 60)
    print(f"Certified tables used: {', '.join(result.tables_used) or 'none'}")
    print(f"Tool calls: {len(result.tool_calls)}  |  blocked queries: {len(result.blocked)}  |  turns: {result.turns}")
    for b in result.blocked:
        print(f"  blocked -> {b.summary}")
    if a.show_sql:
        for i, q in enumerate(result.queries, 1):
            print(f"\n-- query {i}\n{q}")


if __name__ == "__main__":
    main()
