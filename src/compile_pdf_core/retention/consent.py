"""Consent flag parsing + tenant resolution for retention-for-training.

The opt-in signal arrives one of two ways:

* Header ``X-Compile-Retain-For-Training`` — accepted on every
  producer endpoint.
* Form field ``retain_for_training`` — accepted only on multipart
  endpoints (e.g. ``POST /v1/marks/apply-multipart``); JSON endpoints
  ignore form data.

Only ``true`` / ``1`` / ``yes`` (case-insensitive, trimmed) count as
opt-in. Anything else — including malformed values, the literal
string ``"false"``, empty strings, and the header being absent — is
a default-off opt-out. Header takes precedence over form field; when
both are set, the form field is consulted only if the header is
absent entirely.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


CONSENT_HEADER = "X-Compile-Retain-For-Training"
CONSENT_FORM_FIELD = "retain_for_training"
TENANT_HEADER = "X-Compile-Tenant"

_TRUTHY = frozenset({"true", "1", "yes"})


def parse_consent(request: Request, form_value: str | None = None) -> bool:
    """Return the request's retention-for-training opt-in decision.

    ``form_value`` is the value of the multipart form field, when the
    endpoint accepts one. JSON endpoints pass ``None`` and the
    decision is driven by the header alone.
    """
    header = request.headers.get(CONSENT_HEADER)
    if header is not None:
        return _is_truthy(header)
    if form_value is not None:
        return _is_truthy(form_value)
    return False


def _is_truthy(raw: str) -> bool:
    return raw.strip().lower() in _TRUTHY


def resolve_tenant(request: Request) -> str:
    """Resolve the tenant identifier for retention object keys.

    Reads :data:`TENANT_HEADER` and slugifies it. Missing / empty
    header → ``"anonymous"``. Slugification strips characters that
    aren't safe for S3 keys; the goal is "operator-controlled label"
    not "authenticated identity".
    """
    raw = (request.headers.get(TENANT_HEADER) or "").strip()
    if not raw:
        return "anonymous"
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-_").lower()
    return slug or "anonymous"


def retention_configured() -> bool:
    """True when :envvar:`COMPILE_RETAIN_BUCKET` is set to a non-empty
    value. Missing config → retention is silently disabled even on
    opt-in calls."""
    return bool((os.environ.get("COMPILE_RETAIN_BUCKET") or "").strip())


__all__ = [
    "CONSENT_FORM_FIELD",
    "CONSENT_HEADER",
    "TENANT_HEADER",
    "parse_consent",
    "resolve_tenant",
    "retention_configured",
]
