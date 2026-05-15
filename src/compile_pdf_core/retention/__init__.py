"""Retention-for-training — opt-in persistence of producer inputs/outputs.

Engine-side counterpart to the marketing site's "help improve the
engine" checkbox. When the request carries an explicit opt-in
signal (header :data:`CONSENT_HEADER` or, on multipart endpoints,
form field :data:`CONSENT_FORM_FIELD`) **and** the operator has
configured :envvar:`COMPILE_RETAIN_BUCKET`, the producer endpoint
persists three blobs per call:

* ``input.pdf``  — the raw request bytes
* ``output.pdf`` — the producer's output bytes
* ``result.json`` — the producer's JSON response with
  ``output_pdf_b64`` stripped (the bytes already live in
  ``output.pdf``, no need to duplicate)

Object keys follow ``{prefix}/{tenant}/{producer}/{YYYY-MM-DD}/
{input_sha256}/{name}``. Bucket-side lifecycle policy honours the
``ttl-days`` tag, so the engine never runs its own GC.

Default behaviour with no env config: noop. Default behaviour with
env configured but the request flag false / missing: noop. The
consent decision is logged on every producer apply regardless.
"""

from __future__ import annotations

from compile_pdf_core.retention.consent import (
    CONSENT_FORM_FIELD,
    CONSENT_HEADER,
    TENANT_HEADER,
    parse_consent,
    resolve_tenant,
)
from compile_pdf_core.retention.store import (
    RetentionBackendError,
    RetentionStore,
    delete_by_sha256,
    persist_if_opted_in,
)

__all__ = [
    "CONSENT_FORM_FIELD",
    "CONSENT_HEADER",
    "RetentionBackendError",
    "RetentionStore",
    "TENANT_HEADER",
    "delete_by_sha256",
    "parse_consent",
    "persist_if_opted_in",
    "resolve_tenant",
]
