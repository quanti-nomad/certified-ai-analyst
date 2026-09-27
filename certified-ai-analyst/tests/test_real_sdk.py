"""Run the agent through the real Anthropic SDK with only the HTTP layer faked,
so response parsing and passing SDK objects back into the next request are covered."""

import json
from pathlib import Path

import anthropic
try:  # newer SDK releases ship their HTTP client as httpx2
    import httpx2 as httpx
except ImportError:
    import httpx

from analyst.agent import CertifiedAnalyst

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "catalog/semantic_catalog.json").read_text())


def message(content, stop):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "test", "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_loop_with_real_sdk_objects():
    replies = [
        message([{"type": "tool_use", "id": "toolu_1", "name": "run_query",
                  "input": {"sql": "select count(*) from certified.dim_location", "purpose": "count"}}], "tool_use"),
        message([{"type": "text", "text": "There are 24 locations."}], "end_turn"),
    ]
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=replies[len(sent) - 1])

    client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = CertifiedAnalyst(client, CATALOG, ROOT / "data/certified_demo.duckdb").ask("How many locations?")

    assert result.answer == "There are 24 locations."
    second = sent[1]["messages"]
    assert second[1]["content"][0]["type"] == "tool_use"          # SDK object serialized back correctly
    assert json.loads(second[2]["content"][0]["content"])["rows"] == [[24]]
    assert sent[0]["tools"][2]["name"] == "run_query"
