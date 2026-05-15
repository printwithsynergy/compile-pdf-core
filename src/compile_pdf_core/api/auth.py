"""Authentication modes for compile-pdf API.

Lifts the codex_pdf.api.auth surface verbatim per spec §1.10 — five modes
selected via ``COMPILE_AUTH_MODE`` (comma-separated subset of
``none``, ``bearer``, ``api-key``, ``internal``, ``basic``).

Reuse rationale: codex's auth surface is already proven against the same
threat model (internal calls + public-facing marketing demos). Lifting it
verbatim minimizes new attack surface and keeps operator muscle memory
uniform across codex/compile.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterable

from fastapi import HTTPException, Request, status

ALL_MODES = frozenset({"none", "bearer", "api-key", "internal", "basic"})


def get_active_modes() -> frozenset[str]:
    """Read ``COMPILE_AUTH_MODE`` env var; default to ``none`` if unset."""
    raw = os.environ.get("COMPILE_AUTH_MODE", "").strip()
    if not raw:
        return frozenset({"none"})
    requested = {token.strip().lower() for token in raw.split(",") if token.strip()}
    invalid = requested - ALL_MODES
    if invalid:
        raise RuntimeError(
            f"COMPILE_AUTH_MODE contains unknown modes: {sorted(invalid)} "
            f"(valid: {sorted(ALL_MODES)})"
        )
    return frozenset(requested)


def _check_bearer(authorization: str | None) -> bool:
    expected = os.environ.get("COMPILE_BEARER_TOKEN", "")
    if not expected or not authorization:
        return False
    if not authorization.lower().startswith("bearer "):
        return False
    presented = authorization[len("Bearer ") :].strip()
    return secrets.compare_digest(presented.encode(), expected.encode())


def _check_api_key(api_key: str | None) -> bool:
    expected = os.environ.get("COMPILE_API_KEY", "")
    if not expected or not api_key:
        return False
    return secrets.compare_digest(api_key.encode(), expected.encode())


def _check_internal(internal_token: str | None) -> bool:
    expected = os.environ.get("COMPILE_INTERNAL_TOKEN", "")
    if not expected or not internal_token:
        return False
    return secrets.compare_digest(internal_token.encode(), expected.encode())


def _check_basic(authorization: str | None) -> bool:
    if os.environ.get("COMPILE_BASIC_AUTH_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return False
    expected_user = os.environ.get("COMPILE_BASIC_AUTH_USER", "")
    expected_pass = os.environ.get("COMPILE_BASIC_AUTH_PASS", "")
    if not expected_user or not expected_pass or not authorization:
        return False
    if not authorization.lower().startswith("basic "):
        return False
    import base64

    try:
        decoded = base64.b64decode(authorization[len("Basic ") :]).decode("utf-8")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    user, _, pwd = decoded.partition(":")
    return secrets.compare_digest(user.encode(), expected_user.encode()) and secrets.compare_digest(
        pwd.encode(), expected_pass.encode()
    )


def authenticate(request: Request, _modes: Iterable[str] | None = None) -> str:
    """Dependency for FastAPI routes that require authentication.

    Returns the mode that succeeded, raises 401 if all configured modes fail.
    Healthcheck routes opt out by not declaring this dependency.
    """
    modes = frozenset(_modes) if _modes is not None else get_active_modes()
    if "none" in modes:
        return "none"

    if "bearer" in modes and _check_bearer(request.headers.get("Authorization")):
        return "bearer"
    if "api-key" in modes and _check_api_key(request.headers.get("X-Compile-Key")):
        return "api-key"
    if "internal" in modes and _check_internal(request.headers.get("X-Compile-Internal")):
        return "internal"
    if "basic" in modes and _check_basic(request.headers.get("Authorization")):
        return "basic"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
