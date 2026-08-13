"""MCP server tests, driven through the protocol surface rather than the functions.

Calling the tool functions directly would prove nothing about registration: a tool that is
never registered, or one whose schema omits a required argument, still works when imported.
So these go through `server.call_tool` / `server.list_tools` the way a client does.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from grounded_context.mcp_server import server
from grounded_context.provenance import NOT_FOUND
from grounded_context.service import load_bundle, lookup_field

FRESH = date(2026, 8, 11)


def call(name: str, **arguments: Any) -> dict[str, Any]:
    """Invoke a tool as a client would, asserting the call itself succeeded."""
    result = asyncio.run(server.call_tool(name, arguments))
    assert result.is_error is False, result.content
    assert result.structured_content is not None
    return result.structured_content


def tools() -> dict[str, Any]:
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def test_the_three_tools_are_registered_and_described() -> None:
    registered = tools()
    assert set(registered) == {
        "lookup_canonical_fact",
        "ask_grounded",
        "list_entities",
    }
    # Descriptions carry the "never answer exact facts from memory" contract to the model.
    assert all(registered[name].description for name in registered)


def test_lookup_schema_marks_the_entity_and_field_required() -> None:
    schema = tools()["lookup_canonical_fact"].input_schema
    assert set(schema["required"]) == {"entity_id", "field"}
    assert "as_of" in schema["properties"]


def test_lookup_tool_returns_the_library_envelope_unchanged() -> None:
    """The MCP layer must not become a second brain — same envelope, plus rendering."""
    structured = call(
        "lookup_canonical_fact",
        entity_id="anthropic.claude-opus-5",
        field="context_window_tokens",
        as_of=FRESH.isoformat(),
    )
    expected = lookup_field(
        load_bundle(), "anthropic.claude-opus-5", "context_window_tokens", FRESH
    )
    assert {k: v for k, v in structured.items() if k != "rendered"} == expected


def test_lookup_tool_carries_the_citation_block() -> None:
    structured = call(
        "lookup_canonical_fact",
        entity_id="anthropic.claude-opus-5",
        field="context_window_tokens",
        as_of=FRESH.isoformat(),
    )
    assert structured["answer"] == "1,000,000"
    assert "human-reviewed 2026-08-10" in structured["rendered"]
    assert "fresh until 2026-09-09" in structured["rendered"]


def test_lookup_tool_traverses_one_hop() -> None:
    structured = call(
        "lookup_canonical_fact", entity_id="anthropic.claude-opus-5", field="method"
    )
    assert structured["answer"] == "POST"
    assert structured["citations"][0]["hops"] == [
        "anthropic.claude-opus-5",
        "anthropic.messages",
    ]


def test_ask_tool_routes_a_precision_question_deterministically() -> None:
    structured = call(
        "ask_grounded", query="What is the exact context window of claude-opus-5?"
    )
    assert structured["router"]["route"] == "DETERMINISTIC"
    assert structured["answer"] == "1,000,000"


def test_refusal_is_a_grounded_result_not_a_protocol_error() -> None:
    """An exploratory question must come back as a clean refusal, not a tool failure."""
    structured = call("ask_grounded", query="How should I chunk documents for retrieval?")
    assert structured["router"]["route"] == "SEMANTIC"
    assert structured["answer"] == NOT_FOUND
    assert structured["citations"] == []


def test_unknown_field_refuses_rather_than_guessing() -> None:
    structured = call(
        "lookup_canonical_fact",
        entity_id="anthropic.claude-opus-5",
        field="rate_limit_rpm",
    )
    assert structured["answer"] == NOT_FOUND


def test_as_of_surfaces_staleness_over_mcp_too() -> None:
    structured = call(
        "lookup_canonical_fact",
        entity_id="anthropic.claude-opus-5",
        field="context_window_tokens",
        as_of="2026-10-01",
    )
    assert structured["citations"][0]["is_stale"] is True
    assert "⚠ STALE since 2026-09-09" in structured["rendered"]


def test_list_entities_tool_exposes_the_bundle() -> None:
    entities = call("list_entities")["entities"]
    assert [entity["id"] for entity in entities] == [
        "anthropic.claude-haiku-4-5",
        "anthropic.claude-opus-5",
        "anthropic.claude-sonnet-5",
        "anthropic.messages",
    ]
    opus = next(e for e in entities if e["id"] == "anthropic.claude-opus-5")
    assert "context_window_tokens" in opus["canonical_fields"]
    assert opus["trust_tier"] == "human-reviewed"
