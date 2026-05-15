"""Consent parser + tenant resolver for retention-for-training."""

from __future__ import annotations

from typing import Any

import pytest

from compile_pdf_core.retention.consent import (
    CONSENT_HEADER,
    TENANT_HEADER,
    parse_consent,
    resolve_tenant,
    retention_configured,
)


class _FakeRequest:
    """Minimal Request stand-in. ``parse_consent``/``resolve_tenant`` only
    poke ``.headers.get``; no need for the full Starlette object."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def _r(**headers: str) -> Any:
    return _FakeRequest(headers=headers)


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", " YES "])
def test_parse_consent_truthy_header(value: str) -> None:
    assert parse_consent(_r(**{CONSENT_HEADER: value})) is True


@pytest.mark.parametrize(
    "value",
    ["false", "0", "no", "", "  ", "2", "maybe", "True!"],
)
def test_parse_consent_falsy_header(value: str) -> None:
    assert parse_consent(_r(**{CONSENT_HEADER: value})) is False


def test_parse_consent_missing_header_defaults_off() -> None:
    assert parse_consent(_r()) is False


def test_parse_consent_form_value_used_when_header_absent() -> None:
    assert parse_consent(_r(), form_value="true") is True
    assert parse_consent(_r(), form_value="false") is False
    assert parse_consent(_r(), form_value=None) is False


def test_parse_consent_header_takes_precedence_over_form() -> None:
    """Header set to truthy → True regardless of form value, and vice versa."""
    assert parse_consent(_r(**{CONSENT_HEADER: "true"}), form_value="false") is True
    assert parse_consent(_r(**{CONSENT_HEADER: "false"}), form_value="true") is False


def test_resolve_tenant_missing_header_is_anonymous() -> None:
    assert resolve_tenant(_r()) == "anonymous"
    assert resolve_tenant(_r(**{TENANT_HEADER: ""})) == "anonymous"
    assert resolve_tenant(_r(**{TENANT_HEADER: "   "})) == "anonymous"


def test_resolve_tenant_slugifies() -> None:
    assert resolve_tenant(_r(**{TENANT_HEADER: "Print With Synergy"})) == "print-with-synergy"
    assert resolve_tenant(_r(**{TENANT_HEADER: "acme/co"})) == "acme-co"
    assert resolve_tenant(_r(**{TENANT_HEADER: "User_42"})) == "user_42"


def test_resolve_tenant_strips_to_anonymous_when_unsafe_only() -> None:
    assert resolve_tenant(_r(**{TENANT_HEADER: "///"})) == "anonymous"
    assert resolve_tenant(_r(**{TENANT_HEADER: "!@#$%"})) == "anonymous"


def test_retention_configured_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPILE_RETAIN_BUCKET", raising=False)
    assert retention_configured() is False
    monkeypatch.setenv("COMPILE_RETAIN_BUCKET", "")
    assert retention_configured() is False
    monkeypatch.setenv("COMPILE_RETAIN_BUCKET", "my-bucket")
    assert retention_configured() is True
