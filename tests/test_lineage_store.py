"""Lineage store — memory backend + selector."""

from __future__ import annotations

import pytest

from compile_pdf_core.lineage.store import (
    LineageBackendError,
    LineageNotFoundError,
    LineageStep,
    MemoryLineageStore,
    RedisLineageStore,
    S3LineageStore,
    select_store,
    serialize_chain,
)


def _step(lid: str, idx: int, producer: str = "rewrite") -> LineageStep:
    return LineageStep(
        lineage_id=lid,
        step_index=idx,
        producer=producer,
        input_sha256="a" * 64,
        output_sha256="b" * 64,
        cache_key="c" * 64,
        plan_sha256="d" * 64,
    )


def test_memory_store_round_trips() -> None:
    store = MemoryLineageStore()
    store.put(_step("job-1", 0, "rewrite"))
    store.put(_step("job-1", 1, "marks"))
    chain = store.get("job-1")
    assert chain.lineage_id == "job-1"
    assert [s.producer for s in chain.steps] == ["rewrite", "marks"]


def test_memory_store_orders_steps_by_index() -> None:
    """Insertion order is permuted; retrieval should sort by step_index."""
    store = MemoryLineageStore()
    store.put(_step("job-2", 1, "marks"))
    store.put(_step("job-2", 0, "rewrite"))
    chain = store.get("job-2")
    assert [s.step_index for s in chain.steps] == [0, 1]


def test_memory_store_raises_for_missing_lineage_id() -> None:
    store = MemoryLineageStore()
    with pytest.raises(LineageNotFoundError):
        store.get("nope")


def test_list_ids_respects_limit() -> None:
    store = MemoryLineageStore()
    for i in range(10):
        store.put(_step(f"job-{i}", 0))
    assert len(store.list_ids(limit=3)) == 3


def test_select_store_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPILE_LINEAGE_BACKEND", raising=False)
    assert isinstance(select_store(), MemoryLineageStore)
    assert isinstance(select_store("memory"), MemoryLineageStore)


def test_select_store_s3_requires_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPILE_LINEAGE_S3_BUCKET", raising=False)
    with pytest.raises(LineageBackendError, match="bucket"):
        select_store("s3")


def test_select_store_s3_with_bucket_returns_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPILE_LINEAGE_S3_BUCKET", "compile-lineage")
    monkeypatch.setenv("COMPILE_LINEAGE_S3_PREFIX", "lineage")
    store = select_store("s3")
    assert isinstance(store, S3LineageStore)


def test_select_store_redis_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPILE_LINEAGE_REDIS_URL", raising=False)
    with pytest.raises(LineageBackendError, match="URL"):
        select_store("redis")


def test_select_store_redis_with_url_returns_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPILE_LINEAGE_REDIS_URL", "redis://localhost:6379/0")
    store = select_store("redis")
    assert isinstance(store, RedisLineageStore)


def test_select_store_rejects_unknown_backend() -> None:
    with pytest.raises(LineageBackendError, match="unknown lineage backend"):
        select_store("dynamodb")


def test_select_store_reads_env_when_arg_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_LINEAGE_BACKEND", "memory")
    assert isinstance(select_store(), MemoryLineageStore)


def test_serialize_chain_emits_step_records() -> None:
    store = MemoryLineageStore()
    store.put(_step("job-3", 0))
    store.put(_step("job-3", 1, "marks"))
    chain = store.get("job-3")
    serialized = serialize_chain(chain)
    assert serialized["lineage_id"] == "job-3"
    assert len(serialized["steps"]) == 2
    assert serialized["steps"][0]["producer"] == "rewrite"
