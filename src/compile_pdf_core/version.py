"""Package version + per-producer + GJD schema versions.

Per spec §6.2 — each producer's schema version bumps independently
(additive ``1.x`` within current major; breaking → ``/v2/...``).
The codex-pdf wheel version Compile was built against is captured at
runtime in /healthz and in lineage records.

Loose coupling rationale: rewrite can ship a feature in ``1.1.0``
without forcing marks/impose/trap to also bump.
"""

from __future__ import annotations

VERSION = "0.5.5"
"""Compile-PDF package version (semver). Bumped on every release."""

REWRITE_SCHEMA_VERSION = "1.0.0"
"""Schema version for rewrite-plan documents and ``POST /v1/rewrite/apply`` response shape."""

MARKS_SCHEMA_VERSION = "1.0.0"
"""Schema version for marks-template documents and ``POST /v1/marks/apply`` response shape."""

IMPOSE_SCHEMA_VERSION = "1.0.0"
"""Schema version for impose-plan documents and ``POST /v1/impose/apply`` response shape."""

TRAP_SCHEMA_VERSION = "1.0.0"
"""Schema version for trap-policy documents, ``POST /v1/trap/apply``,
and the trap-diff artifact shape."""

CJD_SCHEMA_VERSION = "1.0.0"
"""Schema version for the Compile Job Definition (CJD) format —
the JSON/XML envelope that bundles a multi-producer job into one
submission. See spec §4.5.2."""

COMPILE_DOCUMENT_SCHEMA_VERSION = "1.0.0"
"""Top-level Compile-document schema version. Bumps when the lineage
record shape itself changes."""

CODEX_DOCUMENT_SCHEMA_VERSION_PIN = "1.3.0"
"""Codex-document schema version Compile is built against. Codex does not
yet publish this as a constant on `codex_pdf`, so we pin it here and
surface it via /v1/healthz and /v1/contract for operators. Bump alongside
codex when the codex-document model shape changes (codex 1.x line keeps
this at 1.0.0)."""

PRODUCER_SCHEMA_VERSIONS: dict[str, str] = {
    "rewrite": REWRITE_SCHEMA_VERSION,
    "marks": MARKS_SCHEMA_VERSION,
    "impose": IMPOSE_SCHEMA_VERSION,
    "trap": TRAP_SCHEMA_VERSION,
    "cjd": CJD_SCHEMA_VERSION,
}
"""Aggregate map exposed via ``GET /v1/contract.producer_schema_versions``."""
