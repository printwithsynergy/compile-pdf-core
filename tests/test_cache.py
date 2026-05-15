"""Cache canonicalization + cache-key tests.

Spec §1.6 / §1.6a: identical inputs MUST produce identical cache keys
across runs and Python versions; cosmetic differences (key order,
comments, null fields, number formatting) MUST NOT affect the digest.
"""

from __future__ import annotations

from compile_pdf_core.cache import canonicalize_plan, compute_cache_key, hash_canonical_plan


def test_canonicalize_sorts_keys_recursively():
    original = {"b": 1, "a": {"z": 2, "y": 3}, "c": [{"k2": 0, "k1": 1}]}
    canonical = canonicalize_plan(original)
    assert list(canonical.keys()) == ["a", "b", "c"]
    assert list(canonical["a"].keys()) == ["y", "z"]
    assert list(canonical["c"][0].keys()) == ["k1", "k2"]


def test_canonicalize_strips_decorative_keys():
    plan = {"comment": "ignore me", "notes": "also ignore", "_dev_meta": {"by": "x"}, "real": 1}
    assert canonicalize_plan(plan) == {"real": 1}


def test_canonicalize_drops_null_values():
    plan = {"a": None, "b": 1, "c": {"d": None}}
    assert canonicalize_plan(plan) == {"b": 1, "c": {}}


def test_canonicalize_normalizes_floats():
    # Different float representations of the same value must canonicalize identically.
    plan_a = {"distance_pt": 0.144}
    plan_b = {"distance_pt": 0.14400000000000}
    assert hash_canonical_plan(plan_a) == hash_canonical_plan(plan_b)


def test_canonicalize_distinguishes_real_differences():
    plan_a = {"distance_pt": 0.144}
    plan_b = {"distance_pt": 0.288}
    assert hash_canonical_plan(plan_a) != hash_canonical_plan(plan_b)


_CACHE_KEY_BASE = {
    "producer": "rewrite",
    "input_sha256": "a" * 64,
    "canonical_plan_sha256": "b" * 64,
    "codex_pdf_package_version": "1.4.2",
    "color_schema_version": "1.0.0",
    "geom_schema_version": "1.0.0",
    "codex_document_schema_version": "1.0.0",
    "compile_version": "0.1.0",
}


def test_compute_cache_key_reproducible():
    assert compute_cache_key(**_CACHE_KEY_BASE) == compute_cache_key(**_CACHE_KEY_BASE)


def test_compute_cache_key_changes_with_section_version():
    bumped = {**_CACHE_KEY_BASE, "color_schema_version": "1.1.0"}
    assert compute_cache_key(**_CACHE_KEY_BASE) != compute_cache_key(**bumped), (
        "section bump must invalidate cache key (§1.6a)"
    )


def test_compute_cache_key_changes_with_compile_version():
    bumped = {**_CACHE_KEY_BASE, "compile_version": "0.2.0"}
    assert compute_cache_key(**_CACHE_KEY_BASE) != compute_cache_key(**bumped)
