"""End-to-end CLI tests.

These exist because the first CLI run failed on an import-shadowing bug that four
module-level test files all missed: `__init__.py` re-exports a function named
`lookup`, which shadows the `lookup` submodule. Nothing that imports submodules
directly can catch that — only running the entry point can.
"""

from pathlib import Path

import pytest

from grounded_context import service
from grounded_context.cli import main
from grounded_context.es_client import is_configured

requires_elasticsearch = pytest.mark.skipif(
    not is_configured(), reason="no ES_URL / ES_API_KEY — semantic path unavailable"
)


def test_lookup_exact_fact(capsys):
    assert main(["lookup", "anthropic.claude-opus-5", "context_window_tokens"]) == 0
    out = capsys.readouterr().out
    assert "Answer: 1,000,000" in out
    assert "canonical.context_window_tokens" in out
    assert "fresh until 2026-11-30" in out


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


def test_exploratory_question_refuses_when_elasticsearch_is_absent(capsys, monkeypatch):
    """No index reachable means no grounded source — refuse rather than fall back."""
    monkeypatch.setattr(service, "semantic_citations", lambda query, size=5: service.SemanticResult())
    assert main(["ask", "How should I chunk documents for retrieval?"]) == 1
    out = capsys.readouterr().out
    assert "router: SEMANTIC" in out
    assert "Not found in the grounded sources." in out


@requires_elasticsearch
def test_exploratory_question_returns_grounded_passages(capsys):
    """Same question, index reachable: passages with scores, never a bare assertion."""
    assert main(["ask", "How should I chunk documents for retrieval?"]) == 0
    out = capsys.readouterr().out
    assert "router: SEMANTIC" in out
    assert "Top passage:" in out
    assert "hybrid(bm25+elser,rrf)" in out
    assert "score " in out


def test_unknown_fact_refuses_rather_than_guessing(capsys):
    assert main(["lookup", "anthropic.claude-opus-5", "rate_limit_rpm"]) == 1
    assert "Not found in the grounded sources." in capsys.readouterr().out


def test_as_of_surfaces_staleness_without_faking_data(capsys):
    assert (
        main(
            [
                "--as-of",
                "2027-01-01",
                "lookup",
                "anthropic.claude-opus-5",
                "context_window_tokens",
            ]
        )
        == 0
    )
    assert "⚠ STALE since 2026-11-30" in capsys.readouterr().out


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


def test_telemetry_summary_reads_the_log_with_no_cluster(capsys):
    """The demo beat that cannot fail on a cluster blip: the readback is local."""
    sample = Path(__file__).resolve().parent / "data" / "telemetry-sample.ndjson"
    assert main(["telemetry", "summary", "--log", str(sample)]) == 0

    out = capsys.readouterr().out
    assert "events: 26" in out
    assert "miss rate 35% of 14 precision queries" in out


def test_telemetry_needs_a_subcommand(capsys):
    """`gctx telemetry` alone is a usage error, not a silent no-op."""
    with pytest.raises(SystemExit) as exit_code:
        main(["telemetry"])
    assert exit_code.value.code == 2


# --- errors are reported, not raised (ELX-47) -----------------------------------------------


class _ClusterError(Exception):
    """Stands in for anything the Elasticsearch client raises."""


def test_a_missing_configuration_is_a_message_not_a_traceback(capsys, monkeypatch):
    """`gctx eval` reaches the cluster directly, so `is_configured()` never guards it.

    It exited on a raw `ElasticsearchNotConfigured` stack trace while `gctx telemetry index`
    printed a usable sentence for the identical condition. Reporting one and dumping a traceback
    for the other tells the reader the second is a bug in the tool.
    """
    from grounded_context import es_client

    def unconfigured():
        raise es_client.ElasticsearchNotConfigured("missing ES_URL")

    monkeypatch.setattr(es_client, "credentials", unconfigured)

    assert main(["eval", "--compare", "rank_constant"]) == 2
    assert capsys.readouterr().err.strip() == "error: missing ES_URL"


def test_a_cluster_failure_is_a_message_not_a_traceback(capsys, monkeypatch):
    from grounded_context import cli, es_client

    monkeypatch.setattr(es_client, "transport_errors", lambda: (_ClusterError,))
    monkeypatch.setattr(
        cli, "cmd_eval", lambda args: (_ for _ in ()).throw(_ClusterError("Connection error"))
    )

    assert main(["eval"]) == 2
    assert capsys.readouterr().err.strip() == (
        "error: Elasticsearch: _ClusterError: Connection error"
    )


def test_an_unexpected_error_still_raises(monkeypatch):
    """Only the cluster's failures are turned into a sentence. A bug must still be a traceback."""
    from grounded_context import cli

    monkeypatch.setattr(cli, "cmd_route", lambda args: (_ for _ in ()).throw(KeyError("bug")))

    with pytest.raises(KeyError):
        main(["route", "anything"])


# --- the no-cluster guard on `gctx eval` -------------------------------------------------------


def test_eval_says_so_when_no_cluster_is_configured(monkeypatch, capsys) -> None:
    """A fresh clone runs `gctx eval` before anything else, and seven cases fail without a cluster.

    The README quotes a full-cluster run. Without this banner the only conclusion available to
    someone who just cloned the repo is that the published numbers do not reproduce.
    """
    monkeypatch.setattr("grounded_context.es_client.is_configured", lambda: False)
    # Exit 1, not 0: seven cases really do fail. The banner explains the failures, it does not
    # excuse them — a green exit on a half-run eval would be the worse lie.
    assert main(["eval"]) == 1
    err = capsys.readouterr().err
    assert "no Elasticsearch configured" in err
    assert "docs/data/eval.json" in err


def test_a_missing_es_extra_is_a_message_not_a_traceback(monkeypatch, capsys) -> None:
    """A bare install has no `elasticsearch`, and `gctx eval --compare` dumped the import error.

    A fresh clone hit this: `ModuleNotFoundError: No module named 'elasticsearch'` with a full
    stack, while `gctx telemetry index` printed a usable sentence for the same missing extra.
    `client()` now raises the domain error that `main()` already knows how to report.
    """
    import builtins

    real_import = builtins.__import__

    def no_elasticsearch(name, *args, **kwargs):
        if name == "elasticsearch":
            raise ModuleNotFoundError("No module named 'elasticsearch'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_elasticsearch)
    assert main(["eval", "--compare", "rank_constant"]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "es` extra is not installed" in err


def test_a_missing_bundle_names_the_way_out(tmp_path) -> None:
    """The default bundle path assumes a source checkout. When that is false, say what to do.

    A non-editable `pip install` resolves the default to a directory inside site-packages that
    does not exist, because knowledge/ is not package data.
    """
    from grounded_context.bundle import Bundle, BundleError

    with pytest.raises(BundleError) as excinfo:
        Bundle.load(tmp_path / "absent")
    message = str(excinfo.value)
    assert "--bundle" in message and "GC_BUNDLE" in message
    assert "editable" in message
