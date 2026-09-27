"""The analyst: a Claude tool-use loop over certified data only."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .tools import TOOL_SPECS, ToolCall, Toolbox

DEFAULT_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are a data analyst for a multi-location clinic chain. You answer business questions for executives and the board.

Rules you must follow:
1. Use ONLY certified data, reached through your tools. Never answer a data question from memory or assumption.
2. For any metric, call list_kpis and use the official KPI definition. If the question uses a term that maps to a KPI (e.g. "churn", "revenue", "conversion"), use that KPI's definition and say which one you used.
3. Prefer metrics.kpi_location_monthly for KPI questions; its columns already implement the KPI Bible. Only go to certified fact tables when the KPI table cannot answer.
4. When combining rates across locations or months, recompute them from their underlying counts (for example, churned_members / (active_members - new_members)); never average rates.
5. If the question needs data that is not in the certified tables (for example patient names, emails, clinical notes, staff data or anything uncertified), say clearly that it is not available in certified data. Do not approximate it with other data.
6. If a query is blocked, read the reason and correct course. Never try to work around a guardrail.

Answer format:
- Lead with the direct answer in one or two sentences, with the key numbers.
- Then a short "How I got this" line: the KPI definition(s) and table(s) used.
- Note any caveat that changes how the number should be read (partial month, small sample, closed location).
Keep it short and plain. Use a small table only when comparing several locations or periods.
"""


@dataclass
class AnalystAnswer:
    question: str
    answer: str
    model: str
    tool_calls: list[ToolCall]
    turns: int
    stopped_reason: str

    @property
    def tables_used(self) -> list[str]:
        return sorted({t for c in self.tool_calls for t in c.tables})

    @property
    def queries(self) -> list[str]:
        return [c.sql_executed for c in self.tool_calls if c.sql_executed and c.ok]

    @property
    def blocked(self) -> list[ToolCall]:
        return [c for c in self.tool_calls if c.tool == "run_query" and not c.ok]

    def audit_record(self) -> dict:
        return {
            "at": datetime.now(timezone.utc).isoformat(),
            "question": self.question,
            "answer": self.answer,
            "model": self.model,
            "turns": self.turns,
            "stopped_reason": self.stopped_reason,
            "tables_used": self.tables_used,
            "tool_calls": [asdict(c) for c in self.tool_calls],
        }


class CertifiedAnalyst:
    def __init__(self, client, catalog: dict, db_path: Path, model: str = DEFAULT_MODEL,
                 max_turns: int = 8, audit_log: Path | None = None):
        self.client = client
        self.catalog = catalog
        self.db_path = Path(db_path)
        self.model = model
        self.max_turns = max_turns
        self.audit_log = audit_log

    def ask(self, question: str) -> AnalystAnswer:
        toolbox = Toolbox(self.catalog, self.db_path)
        system = SYSTEM_PROMPT + "\nContext: " + self.catalog["description"]
        messages = [{"role": "user", "content": question}]
        answer, reason, turns = "", "max_turns", 0

        for turns in range(1, self.max_turns + 1):
            response = self.client.messages.create(
                model=self.model, max_tokens=2000, system=system, tools=TOOL_SPECS, messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                answer = "".join(b.text for b in response.content if b.type == "text").strip()
                reason = response.stop_reason or "end_turn"
                break
            results = []
            for block in tool_uses:
                content, is_error = toolbox.run(block.name, block.input or {})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": content, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        else:
            answer = "I could not finish this analysis within the allowed number of steps."

        result = AnalystAnswer(question, answer, self.model, toolbox.audit, turns, reason)
        if self.audit_log:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log.open("a") as f:
                f.write(json.dumps(result.audit_record(), default=str) + "\n")
        return result


def load_catalog(path: Path) -> dict:
    return json.loads(Path(path).read_text())
