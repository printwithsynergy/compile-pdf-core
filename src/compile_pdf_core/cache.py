"""Cache key composition + plan canonicalization.

Per spec §1.6 + §1.6a — cache key components (alphabetical-by-name so
the digest is reproducible across language implementations):

1. ``codex_document_schema_version``
2. ``codex_pdf_package_version``
3. ``color_schema_version``  (codex_pdf.color.COLOR_SCHEMA_VERSION)
4. ``geom_schema_version``  (codex_pdf.geom.GEOM_SCHEMA_VERSION)
5. ``compile_version``
6. ``producer``  (rewrite | marks | impose | trap)
7. ``sha256(canonical_plan)``
8. ``sha256(input_bytes)``

A Codex section bump auto-invalidates affected cached outputs (load-bearing
operational property).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from compile_pdf_core.version import VERSION as COMPILE_VERSION

_PLAN_CANONICAL_NUMBER_QUANTIZE = Decimal("1E-12")
"""Numeric precision used during canonicalization. 12 decimal places is enough
to disambiguate prepress measurements (which rarely exceed ~5 decimals)
without introducing float drift across Python/JS/Go implementations."""

_DROPPED_KEYS = frozenset({"comment", "notes", "_dev_meta"})
"""Keys stripped from canonical plan before hashing.
Operators can decorate plans with these keys for human readability
without the markings affecting the cache key."""


def canonicalize_plan(plan: Mapping[str, Any] | list[Any] | str | int | float | bool | None) -> Any:
    """Return a canonical, sortable, drop-null-decorated copy of a plan.

    Canonicalization steps (per spec §2.2):

    1. Sort all dict keys recursively.
    2. Normalize numbers to fixed-decimal (round-half-even) so different
       JSON serializers produce identical byte sequences.
    3. Strip ``comment`` / ``notes`` / ``_dev_meta`` keys.
    4. Drop ``None`` values (treat as absent).

    Used by :func:`compute_cache_key`. Pure function; no I/O.
    """
    if plan is None:
        return None
    if isinstance(plan, bool):
        # bool must be checked before int (bool is a subclass of int in Python).
        return plan
    if isinstance(plan, int):
        return plan
    if isinstance(plan, float):
        # Round-half-even via Decimal so the digest is portable.
        quantized = (
            Decimal(repr(plan))
            .quantize(_PLAN_CANONICAL_NUMBER_QUANTIZE, rounding=ROUND_HALF_EVEN)
            .normalize()
        )
        as_str = format(quantized, "f")
        # Re-parse so e.g. "1.0" stays a number in JSON, not a string.
        try:
            int_val = int(as_str)
            if "." not in as_str:
                return int_val
        except ValueError:
            pass
        return float(as_str)
    if isinstance(plan, str):
        return plan
    if isinstance(plan, list):
        return [canonicalize_plan(item) for item in plan]
    if isinstance(plan, Mapping):
        return {
            key: canonicalize_plan(value)
            for key, value in sorted(plan.items())
            if key not in _DROPPED_KEYS and value is not None
        }
    raise TypeError(f"Unsupported plan element type: {type(plan)!r}")


def hash_canonical_plan(plan: Mapping[str, Any]) -> str:
    """Return the SHA-256 of a canonicalized plan, hex-encoded.

    The plan is canonicalized via :func:`canonicalize_plan` and then
    serialized with ``json.dumps(..., separators=(",", ":"), ensure_ascii=False,
    sort_keys=False)`` (sort_keys=False is safe because canonicalization already
    sorted recursively).
    """
    canonical = canonicalize_plan(plan)
    serialized = json.dumps(canonical, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_cache_key(
    *,
    producer: str,
    input_sha256: str,
    canonical_plan_sha256: str,
    codex_pdf_package_version: str,
    color_schema_version: str,
    geom_schema_version: str,
    codex_document_schema_version: str,
    compile_version: str = COMPILE_VERSION,
) -> str:
    """Compose the per-job cache key.

    Returns hex-encoded SHA-256. Components are concatenated alphabetical-by-name
    with ``|`` separator so the digest is reproducible across implementations.

    See spec §1.6a for the rationale on each component:

    - ``codex_document_schema_version`` — top-level codex-document schema
    - ``codex_pdf_package_version`` — catches Codex bug fixes without schema bump
    - ``color_schema_version`` — invalidates on /v1/color/* changes
    - ``geom_schema_version`` — invalidates on /v1/geom/* changes
    - ``compile_version`` — captures Compile engine changes
    - ``producer`` — distinguishes the four producer endpoints
    - ``canonical_plan_sha256`` — plan hashed via :func:`hash_canonical_plan`
    - ``input_sha256`` — sha256 of the raw input PDF bytes
    """
    components = "|".join(
        [
            codex_document_schema_version,
            codex_pdf_package_version,
            color_schema_version,
            geom_schema_version,
            compile_version,
            producer,
            canonical_plan_sha256,
            input_sha256,
        ]
    )
    return hashlib.sha256(components.encode("utf-8")).hexdigest()
