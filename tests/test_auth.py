"""Auth mode tests — covers ``compile_pdf.api.auth``'s five modes
and the COMPILE_AUTH_MODE env-var parser.
"""

from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from compile_pdf_core.api import auth


def _request(headers: dict[str, str]) -> Request:
    encoded = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": encoded})


def test_get_active_modes_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPILE_AUTH_MODE", raising=False)
    assert auth.get_active_modes() == frozenset({"none"})


def test_get_active_modes_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_AUTH_MODE", "bearer, api-key , internal")
    modes = auth.get_active_modes()
    assert modes == frozenset({"bearer", "api-key", "internal"})


def test_get_active_modes_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_AUTH_MODE", "bearer,wat")
    with pytest.raises(RuntimeError, match="unknown modes"):
        auth.get_active_modes()


def test_authenticate_none_short_circuits() -> None:
    request = _request({})
    assert auth.authenticate(request, _modes={"none"}) == "none"


def test_authenticate_bearer_accepts_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_BEARER_TOKEN", "s3cret")
    request = _request({"Authorization": "Bearer s3cret"})
    assert auth.authenticate(request, _modes={"bearer"}) == "bearer"


def test_authenticate_bearer_rejects_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_BEARER_TOKEN", "s3cret")
    request = _request({"Authorization": "Bearer nope"})
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(request, _modes={"bearer"})
    assert exc.value.status_code == 401


def test_authenticate_bearer_rejects_missing_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_BEARER_TOKEN", "s3cret")
    request = _request({})
    with pytest.raises(HTTPException):
        auth.authenticate(request, _modes={"bearer"})


def test_authenticate_bearer_rejects_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPILE_BEARER_TOKEN", raising=False)
    request = _request({"Authorization": "Bearer anything"})
    with pytest.raises(HTTPException):
        auth.authenticate(request, _modes={"bearer"})


def test_authenticate_bearer_requires_scheme_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_BEARER_TOKEN", "s3cret")
    request = _request({"Authorization": "s3cret"})
    with pytest.raises(HTTPException):
        auth.authenticate(request, _modes={"bearer"})


def test_authenticate_api_key_accepts_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_API_KEY", "key-123")
    request = _request({"X-Compile-Key": "key-123"})
    assert auth.authenticate(request, _modes={"api-key"}) == "api-key"


def test_authenticate_api_key_rejects_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_API_KEY", "key-123")
    request = _request({"X-Compile-Key": "wrong"})
    with pytest.raises(HTTPException):
        auth.authenticate(request, _modes={"api-key"})


def test_authenticate_internal_accepts_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_INTERNAL_TOKEN", "internal-tok")
    request = _request({"X-Compile-Internal": "internal-tok"})
    assert auth.authenticate(request, _modes={"internal"}) == "internal"


def test_authenticate_internal_rejects_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPILE_INTERNAL_TOKEN", raising=False)
    request = _request({"X-Compile-Internal": "anything"})
    with pytest.raises(HTTPException):
        auth.authenticate(request, _modes={"internal"})


def test_authenticate_basic_accepts_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_BASIC_AUTH_ENABLED", "1")
    monkeypatch.setenv("COMPILE_BASIC_AUTH_USER", "alice")
    monkeypatch.setenv("COMPILE_BASIC_AUTH_PASS", "wonderland")
    encoded = base64.b64encode(b"alice:wonderland").decode("ascii")
    request = _request({"Authorization": f"Basic {encoded}"})
    assert auth.authenticate(request, _modes={"basic"}) == "basic"


def test_authenticate_basic_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPILE_BASIC_AUTH_ENABLED", raising=False)
    encoded = base64.b64encode(b"alice:wonderland").decode("ascii")
    request = _request({"Authorization": f"Basic {encoded}"})
    with pytest.raises(HTTPException):
        auth.authenticate(request, _modes={"basic"})


def test_authenticate_basic_rejects_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_BASIC_AUTH_ENABLED", "1")
    monkeypatch.setenv("COMPILE_BASIC_AUTH_USER", "alice")
    monkeypatch.setenv("COMPILE_BASIC_AUTH_PASS", "wonderland")
    request = _request({"Authorization": "Basic not-base64-😀"})
    with pytest.raises(HTTPException):
        auth.authenticate(request, _modes={"basic"})


def test_authenticate_basic_rejects_no_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_BASIC_AUTH_ENABLED", "1")
    monkeypatch.setenv("COMPILE_BASIC_AUTH_USER", "alice")
    monkeypatch.setenv("COMPILE_BASIC_AUTH_PASS", "wonderland")
    encoded = base64.b64encode(b"no-colon-here").decode("ascii")
    request = _request({"Authorization": f"Basic {encoded}"})
    with pytest.raises(HTTPException):
        auth.authenticate(request, _modes={"basic"})


def test_authenticate_falls_through_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """If multiple modes are active, the first matching one wins."""
    monkeypatch.setenv("COMPILE_BEARER_TOKEN", "tok")
    request = _request({"X-Compile-Key": "ignored", "Authorization": "Bearer tok"})
    assert auth.authenticate(request, _modes={"bearer", "api-key"}) == "bearer"


def test_authenticate_no_match_raises_401() -> None:
    request = _request({})
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(request, _modes={"bearer"})
    assert exc.value.status_code == 401
    assert exc.value.headers is not None
    assert exc.value.headers.get("WWW-Authenticate") == "Bearer"
