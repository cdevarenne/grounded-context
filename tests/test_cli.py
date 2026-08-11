"""End-to-end CLI tests.

These exist because the first CLI run failed on an import-shadowing bug that four
module-level test files all missed: `__init__.py` re-exports a function named
`lookup`, which shadows the `lookup` submodule. Nothing that imports submodules
directly can catch that — only running the entry point can.
"""

from grounded_context.cli import main


def test_lookup_exact_fact(capsys):
    assert main(["lookup", "anthropic.claude-opus-5", "context_window_tokens"]) == 0
    out = capsys.readouterr().out
    assert "Answer: 1,000,000" in out
    assert "canonical.context_window_tokens" in out
    assert "fresh until 2026-09-09" in out


def test_lookup_traverses_one_hop(capsys):
    assert main(["lookup", "anthropic.claude-opus-5", "method"]) == 0
    out = capsys.readouterr().out
    assert "Answer: POST" in out
    assert "traversed: anthropic.claude-opus-5 → anthropic.messages" in out


def test_ask_routes_and_answers(capsys):
    assert main(["ask", "What is the exact context window of claude-opus-5?"]) == 0
    out = capsys.readouterr().out
    assert "router: DETERMINISTIC" in out
    assert "Answer: 1,000,000" in out


def test_ask_renders_booleans_readably(capsys):
    assert main(["ask", "does claude-haiku-4-5 support adaptive thinking?"]) == 0
    assert "Answer: no" in capsys.readouterr().out


def test_exploratory_question_refuses_while_semantic_path_is_unwired(capsys):
    """eval.md Q11's guardrail, and an honest report of what isn't built."""
    assert main(["ask", "How should I chunk documents for retrieval?"]) == 1
    out = capsys.readouterr().out
    assert "router: SEMANTIC" in out
    assert "Not found in the grounded sources." in out


def test_unknown_fact_refuses_rather_than_guessing(capsys):
    assert main(["lookup", "anthropic.claude-opus-5", "rate_limit_rpm"]) == 1
    assert "Not found in the grounded sources." in capsys.readouterr().out


def test_as_of_surfaces_staleness_without_faking_data(capsys):
    assert (
        main(
            [
                "--as-of",
                "2026-10-01",
                "lookup",
                "anthropic.claude-opus-5",
                "context_window_tokens",
            ]
        )
        == 0
    )
    assert "⚠ STALE since 2026-09-09" in capsys.readouterr().out


def test_json_envelope_is_machine_readable(capsys):
    import json

    assert main(["--json", "lookup", "anthropic.messages", "path"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["answer"] == "/v1/messages"
    assert envelope["retrieval_path"] == "deterministic"
    assert envelope["citations"][0]["trust_tier"] == "human-reviewed"


def test_route_subcommand_explains_itself(capsys):
    assert main(["route", "compare claude-opus-5 and claude-sonnet-5"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("BOTH — ")
    assert "cross-entity comparison" in out


def test_entities_lists_the_bundle(capsys):
    assert main(["entities"]) == 0
    out = capsys.readouterr().out
    assert "anthropic.claude-opus-5  [model]  human-reviewed" in out
    assert "canonical.context_window_tokens" in out


def test_missing_bundle_is_an_error_not_a_crash(capsys):
    assert main(["--bundle", "/nonexistent", "entities"]) == 2
    assert "bundle root not found" in capsys.readouterr().err
