"""S3 lineage backend — covered with an in-memory boto3 stub.

We can't reach a real S3 in CI, so the tests inject a fake client that
mirrors the boto3 surface (`put_object`, `list_objects_v2`,
`get_object`). Keeps the tests fast and deterministic; integration
tests against real S3 live outside the unit suite.
"""

from __future__ import annotations

from typing import Any

import pytest

from compile_pdf_core.lineage.store import (
    LineageNotFoundError,
    LineageStep,
    S3LineageStore,
)


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    """Minimal in-memory stand-in for the boto3 S3 client.

    boto3's S3 surface uses PascalCase kwargs (``Bucket``/``Key``/...);
    the fake mirrors that exactly so the production caller is unchanged.
    Per-file ruff ignore in ``pyproject.toml`` suppresses N802/N803 for
    this module.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.calls.append(("put", {"bucket": Bucket, "key": Key}))
        self.objects[Key] = Body

    def list_objects_v2(
        self, *, Bucket: str, Prefix: str, Delimiter: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("list", {"bucket": Bucket, "prefix": Prefix}))
        contents = [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)]
        if Delimiter == "/":
            common: set[str] = set()
            for k in self.objects:
                if not k.startswith(Prefix):
                    continue
                tail = k[len(Prefix) :]
                if "/" in tail:
                    common.add(Prefix + tail.split("/", 1)[0] + "/")
            return {"CommonPrefixes": [{"Prefix": p} for p in sorted(common)]}
        return {"Contents": contents}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append(("get", {"bucket": Bucket, "key": Key}))
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": _FakeBody(self.objects[Key])}


def _step(lineage_id: str, step_index: int, producer: str = "rewrite") -> LineageStep:
    return LineageStep(
        lineage_id=lineage_id,
        step_index=step_index,
        producer=producer,
        input_sha256="a" * 64,
        output_sha256="b" * 64,
        cache_key="c" * 64,
        plan_sha256="d" * 64,
    )


def test_put_writes_one_object_per_step() -> None:
    fake = FakeS3Client()
    store = S3LineageStore(bucket="bucket-x", prefix="lineage", client=fake)
    store.put(_step("job-1", 0, "rewrite"))
    store.put(_step("job-1", 1, "marks"))
    assert sorted(fake.objects.keys()) == [
        "lineage/job-1/0000.json",
        "lineage/job-1/0001.json",
    ]


def test_get_round_trips_through_s3_keys() -> None:
    fake = FakeS3Client()
    store = S3LineageStore(bucket="bucket-x", client=fake)
    store.put(_step("job-2", 1, "marks"))  # out-of-order put
    store.put(_step("job-2", 0, "rewrite"))
    chain = store.get("job-2")
    assert chain.lineage_id == "job-2"
    assert [s.step_index for s in chain.steps] == [0, 1]
    assert [s.producer for s in chain.steps] == ["rewrite", "marks"]


def test_get_raises_for_missing_lineage_id() -> None:
    fake = FakeS3Client()
    store = S3LineageStore(bucket="bucket-x", client=fake)
    with pytest.raises(LineageNotFoundError):
        store.get("nope")


def test_list_ids_walks_common_prefixes() -> None:
    fake = FakeS3Client()
    store = S3LineageStore(bucket="bucket-x", client=fake)
    for jid in ("alpha", "beta", "gamma"):
        store.put(_step(jid, 0))
    ids = store.list_ids(limit=10)
    assert sorted(ids) == ["alpha", "beta", "gamma"]


def test_serialized_record_carries_lineage_id() -> None:
    """Durable backends must round-trip lineage_id even though
    in-memory representations don't need it on the wire."""
    import json

    fake = FakeS3Client()
    store = S3LineageStore(bucket="bucket-x", client=fake)
    store.put(_step("job-3", 0))
    body = next(iter(fake.objects.values()))
    payload = json.loads(body)
    assert payload["lineage_id"] == "job-3"
    assert payload["step_index"] == 0
