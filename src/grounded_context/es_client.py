"""Elasticsearch connection for the semantic path.

Credentials are read from the environment, or from a gitignored `.env` beside the repo root.
They are never logged and never committed — the deterministic path needs none of this, which
is why the client lives behind the `es` extra.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"

#: The corpus this project curates. An adopter points `ES_INDEX` at their own.
DEFAULT_INDEX = "grounded-context-corpus"

#: The preconfigured ELSER endpoint on Elastic Cloud Serverless. Self-managed clusters name
#: theirs differently — `elser_v2`, or whatever `PUT _inference/sparse_embedding/<id>` created —
#: so this cannot be a constant if the code is to run anywhere but here.
DEFAULT_INFERENCE_ID = ".elser-2-elasticsearch"

INDEX_VAR = "ES_INDEX"
INFERENCE_ID_VAR = "ES_INFERENCE_ID"


class ElasticsearchNotConfigured(RuntimeError):
    """ES_URL or ES_API_KEY is missing — the semantic path cannot run."""


def load_env(path: Path = ENV_FILE) -> None:
    """Load KEY=VALUE lines from `.env` without overriding the real environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def setting(name: str, default: str) -> str:
    """One configurable value: the environment, then a gitignored `.env`, then the default."""
    load_env()
    return os.environ.get(name, "").strip() or default


#: Resolved once at import, matching the JVM port, which resolves at class initialization.
#: Changing the environment afterwards does not move them.
INDEX = setting(INDEX_VAR, DEFAULT_INDEX)
INFERENCE_ID = setting(INFERENCE_ID_VAR, DEFAULT_INFERENCE_ID)


def credentials() -> tuple[str, str]:
    """Return (url, api_key), raising if either is absent. Never logs the key."""
    load_env()
    url = os.environ.get("ES_URL", "").strip()
    api_key = os.environ.get("ES_API_KEY", "").strip()
    if not url or not api_key:
        missing = ", ".join(
            name for name, value in (("ES_URL", url), ("ES_API_KEY", api_key)) if not value
        )
        raise ElasticsearchNotConfigured(
            f"missing {missing} — set it in the environment or in a gitignored .env"
        )
    return url, api_key


def is_configured() -> bool:
    """True when the semantic path has both credentials and its client library.

    Credentials without the `es` extra installed is a real state — a clone with a .env but a
    bare install — and it must read as "unavailable", not crash on import mid-query.
    """
    try:
        credentials()
    except ElasticsearchNotConfigured:
        return False
    return importlib.util.find_spec("elasticsearch") is not None


#: Connection policy, matching the JVM port (JVM-15). A cloud endpoint occasionally refuses a
#: connection under load, and a transient blip that fails a command is indistinguishable from a
#: broken install to anyone who just cloned this.
CONNECTION_OPTIONS: dict[str, Any] = {
    "request_timeout": 60,
    "max_retries": 4,
    # `False` by default, and this is the gap that matters: a connect or read timeout — the exact
    # shape a cloud blip takes — is otherwise not retried at all.
    "retry_on_timeout": True,
    # Already the library default. Set explicitly so it visibly matches the JVM's list and cannot
    # drift away from it if that default ever changes.
    "retry_on_status": (429, 502, 503, 504),
    # Also `0` by default, which means retries fire immediately and hammer a cluster that is
    # already struggling. The JVM uses exponentialBackoff(500ms).
    "retry_backoff_base": 0.5,
}


def client(**kwargs: Any) -> Any:
    """Build an Elasticsearch client. Import is local so the core install stays lean.

    Keyword arguments override :data:`CONNECTION_OPTIONS`, which is how a caller supplies a
    corporate CA bundle (`ca_certs=...`) or tightens a timeout for one call.
    """
    from elasticsearch import Elasticsearch

    url, api_key = credentials()
    return Elasticsearch(url, api_key=api_key, **{**CONNECTION_OPTIONS, **kwargs})
