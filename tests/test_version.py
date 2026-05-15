"""Smoke tests for version constants and producer schema map."""

from __future__ import annotations

import re

from compile_pdf_core.version import (
    CJD_SCHEMA_VERSION,
    COMPILE_DOCUMENT_SCHEMA_VERSION,
    IMPOSE_SCHEMA_VERSION,
    MARKS_SCHEMA_VERSION,
    PRODUCER_SCHEMA_VERSIONS,
    REWRITE_SCHEMA_VERSION,
    TRAP_SCHEMA_VERSION,
    VERSION,
)

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.]+)?$")


def test_version_is_semver():
    assert SEMVER.match(VERSION), f"VERSION {VERSION!r} is not semver"


def test_per_producer_schema_versions_are_semver():
    for name, value in (
        ("rewrite", REWRITE_SCHEMA_VERSION),
        ("marks", MARKS_SCHEMA_VERSION),
        ("impose", IMPOSE_SCHEMA_VERSION),
        ("trap", TRAP_SCHEMA_VERSION),
        ("cjd", CJD_SCHEMA_VERSION),
        ("compile-document", COMPILE_DOCUMENT_SCHEMA_VERSION),
    ):
        assert SEMVER.match(value), f"{name}={value!r} is not semver"


def test_producer_schema_versions_map_complete():
    expected = {"rewrite", "marks", "impose", "trap", "cjd"}
    assert set(PRODUCER_SCHEMA_VERSIONS) == expected
