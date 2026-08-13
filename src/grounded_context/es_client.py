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

INDEX = "grounded-context-corpus"
# Preconfigured ELSER endpoint on Elastic Cloud Serverless.
INFERENCE_ID = ".elser-2-elasticsearch"


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


def client(**kwargs: Any) -> Any:
    """Build an Elasticsearch client. Import is local so the core install stays lean."""
    from elasticsearch import Elasticsearch

    url, api_key = credentials()
    return Elasticsearch(url, api_key=api_key, request_timeout=60, **kwargs)
