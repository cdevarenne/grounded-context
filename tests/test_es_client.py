"""The Elasticsearch connection policy.

Building a client opens no socket, so these run wherever the `es` extra is installed — no
cluster and no credentials needed. What they pin is the retry policy, which is invisible until
a cloud blip turns a passing command into a failing one.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

from grounded_context import es_client

requires_the_es_extra = pytest.mark.skipif(
    importlib.util.find_spec("elasticsearch") is None,
    reason="no `es` extra — the client cannot be constructed",
)

pytestmark = requires_the_es_extra

FAKE = ("https://example.invalid:443", "not-a-real-key")


@pytest.fixture(autouse=True)
def _no_credentials_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructing a client makes no request, so it needs no real endpoint."""
    monkeypatch.setattr(es_client, "credentials", lambda: FAKE)


def library_default(name: str) -> Any:
    """What the client would use if we set nothing — the thing these settings exist to change."""
    from elastic_transport import Transport

    import inspect

    return inspect.signature(Transport.__init__).parameters[name].default


def test_timeouts_are_retried() -> None:
    """The single most important setting here, and the one the library leaves off.

    A connect or read timeout is the shape a transient cloud failure actually takes. Without
    this, `max_retries` never fires for the case that matters.
    """
    assert es_client.client()._retry_on_timeout is True


def test_retries_back_off_instead_of_hammering() -> None:
    """Retrying instantly against a cluster under load makes the problem worse, not better."""
    assert es_client.client()._retry_backoff_base == 0.5


def test_the_policy_matches_the_jvm_port() -> None:
    """The two implementations must fail the same way, not just answer the same way."""
    built = es_client.client()
    assert built._max_retries == 4
    assert built._retry_on_status == (429, 502, 503, 504)


def test_caller_options_override_the_defaults() -> None:
    """This is the seam for a corporate CA bundle: `client(ca_certs=...)` must reach the client."""
    assert es_client.client(request_timeout=5)._request_timeout == 5


@pytest.mark.parametrize(
    ("setting", "ours"),
    [("retry_on_timeout", True), ("retry_backoff_base", 0.5), ("max_retries", 4)],
)
def test_each_setting_actually_changes_something(setting: str, ours: Any) -> None:
    """Guards against a no-op override.

    If this fails because the library default caught up with us, that is good news and the
    override can be dropped — but it should be a decision, not a silent coincidence.
    """
    assert library_default(setting) != ours, (
        f"elastic-transport now defaults {setting} to {ours}; "
        "CONNECTION_OPTIONS no longer changes it"
    )
