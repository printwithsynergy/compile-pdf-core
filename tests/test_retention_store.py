"""Retention store — in-memory boto3 fake covers put_triplet + delete_matching."""

from __future__ import annotations

import json
from typing import Any

import pytest

from compile_pdf_core.retention.store import (
    RetentionBackendError,
    RetentionStore,
    delete_by_sha256,
    persist_if_opted_in,
)


class FakeS3Client:
    """In-memory boto3 stand-in mirroring the PascalCase kwargs."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}

    def put_object(  # noqa: N803 — mirroring boto3
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
        Tagging: str,
    ) -> None:
        self.objects[Key] = {
            "Bucket": Bucket,
            "Body": Body,
            "ContentType": ContentType,
            "Tagging": Tagging,
        }

    def get_paginator(self, name: str) -> _FakePaginator:  # noqa: ARG002
        return _FakePaginator(self.objects)

    def delete_objects(  # noqa: N803 — mirroring boto3
        self, *, Bucket: str, Delete: dict[str, Any]
    ) -> dict[str, Any]:
        deleted = []
        for entry in Delete["Objects"]:
            key = entry["Key"]
            if key in self.objects:
                del self.objects[key]
                deleted.append({"Key": key})
        return {"Deleted": deleted}


class _FakePaginator:
    def __init__(self, objects: dict[str, dict[str, Any]]) -> None:
        self._objects = objects

    def paginate(self, *, Bucket: str, Prefix: str):  # noqa: N803, ARG002
        # One page is enough for tests.
        yield {"Contents": [{"Key": k} for k in self._objects if k.startswith(Prefix)]}


def _store(fake: FakeS3Client, **overrides: Any) -> RetentionStore:
    return RetentionStore(
        bucket="bk",
        prefix=overrides.get("prefix", "retain"),
        ttl_days=overrides.get("ttl_days", 7),
        client=fake,
    )


def test_put_triplet_writes_three_objects_with_ttl_tag() -> None:
    fake = FakeS3Client()
    store = _store(fake)
    keys = store.put_triplet(
        tenant="acme",
        producer="rewrite",
        input_sha256="a" * 64,
        input_bytes=b"input bytes",
        output_bytes=b"output bytes",
        result={"ok": True},
    )
    assert len(keys) == 3
    assert all(k.startswith("retain/acme/rewrite/") for k in keys)
    assert keys[0].endswith("/input.pdf")
    assert keys[1].endswith("/output.pdf")
    assert keys[2].endswith("/result.json")
    for k in keys:
        assert fake.objects[k]["Tagging"] == "ttl-days=7"
    assert fake.objects[keys[0]]["Body"] == b"input bytes"
    assert fake.objects[keys[1]]["Body"] == b"output bytes"
    assert json.loads(fake.objects[keys[2]]["Body"]) == {"ok": True}


def test_put_triplet_noop_without_bucket() -> None:
    store = RetentionStore(bucket="", client=FakeS3Client())
    assert (
        store.put_triplet(
            tenant="acme",
            producer="rewrite",
            input_sha256="a" * 64,
            input_bytes=b"in",
            output_bytes=b"out",
            result={},
        )
        == []
    )


def test_persist_if_opted_in_skips_when_consent_false() -> None:
    fake = FakeS3Client()
    store = _store(fake)
    retained = persist_if_opted_in(
        consent=False,
        producer="rewrite",
        tenant="acme",
        input_bytes=b"i",
        output_bytes=b"o",
        result={"output_pdf_b64": "xxx", "ok": True},
        input_sha256="a" * 64,
        store=store,
    )
    assert retained is False
    assert fake.objects == {}


def test_persist_if_opted_in_strips_output_pdf_b64_from_result() -> None:
    fake = FakeS3Client()
    store = _store(fake)
    retained = persist_if_opted_in(
        consent=True,
        producer="rewrite",
        tenant="acme",
        input_bytes=b"i",
        output_bytes=b"o",
        result={"output_pdf_b64": "BIGBLOB", "pdf_sha256": "xyz"},
        input_sha256="a" * 64,
        store=store,
    )
    assert retained is True
    result_key = next(k for k in fake.objects if k.endswith("result.json"))
    payload = json.loads(fake.objects[result_key]["Body"])
    assert "output_pdf_b64" not in payload
    assert payload == {"pdf_sha256": "xyz"}


def test_persist_if_opted_in_swallows_errors_returns_false() -> None:
    class _Broken(FakeS3Client):
        def put_object(self, **kwargs: Any) -> None:  # noqa: ARG002
            raise RuntimeError("boom")

    store = _store(_Broken())
    retained = persist_if_opted_in(
        consent=True,
        producer="rewrite",
        tenant="acme",
        input_bytes=b"i",
        output_bytes=b"o",
        result={},
        input_sha256="a" * 64,
        store=store,
    )
    assert retained is False


def test_delete_matching_finds_and_bulk_deletes() -> None:
    fake = FakeS3Client()
    store = _store(fake)
    sha = "f" * 64
    store.put_triplet(
        tenant="acme",
        producer="rewrite",
        input_sha256=sha,
        input_bytes=b"i",
        output_bytes=b"o",
        result={},
    )
    # An unrelated key that must survive.
    store.put_triplet(
        tenant="acme",
        producer="rewrite",
        input_sha256="9" * 64,
        input_bytes=b"i",
        output_bytes=b"o",
        result={},
    )
    deleted = store.delete_matching(sha)
    assert len(deleted) == 3
    assert all(sha in k for k in deleted)
    assert all(sha not in k for k in fake.objects)
    # Sibling sha keys remain.
    assert any("9" * 64 in k for k in fake.objects)


def test_delete_by_sha256_errors_when_not_configured() -> None:
    store = RetentionStore(bucket="", client=FakeS3Client())
    with pytest.raises(RetentionBackendError):
        delete_by_sha256("a" * 64, store=store)


def test_delete_matching_noop_when_no_hits() -> None:
    fake = FakeS3Client()
    store = _store(fake)
    assert store.delete_matching("0" * 64) == []
