"""Redis lineage backend — covered with an in-memory redis-py stub."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from compile_pdf_core.lineage.store import (
    LineageNotFoundError,
    LineageStep,
    RedisLineageStore,
)


class FakeRedisClient:
    """Minimal stand-in for the redis-py client."""

    def __init__(self) -> None:
        self.lists: dict[str, list[bytes]] = {}

    def rpush(self, key: str, value: str | bytes) -> int:
        body = value.encode("utf-8") if isinstance(value, str) else value
        self.lists.setdefault(key, []).append(body)
        return len(self.lists[key])

    def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        items = self.lists.get(key, [])
        if end == -1:
            return list(items[start:])
        return list(items[start : end + 1])

    def scan_iter(self, *, match: str, count: int = 50) -> Iterator[bytes]:
        prefix = match.rstrip("*")
        for k in list(self.lists.keys()):
            if k.startswith(prefix):
                yield k.encode("utf-8")


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


def test_put_pushes_onto_redis_list() -> None:
    fake = FakeRedisClient()
    store = RedisLineageStore(url="redis://x", client=fake)
    store.put(_step("job-1", 0, "rewrite"))
    store.put(_step("job-1", 1, "marks"))
    assert "lineage:job-1" in fake.lists
    assert len(fake.lists["lineage:job-1"]) == 2


def test_get_returns_chain_in_order() -> None:
    fake = FakeRedisClient()
    store = RedisLineageStore(url="redis://x", client=fake)
    store.put(_step("job-2", 1, "marks"))
    store.put(_step("job-2", 0, "rewrite"))
    chain = store.get("job-2")
    assert [s.step_index for s in chain.steps] == [0, 1]


def test_get_raises_for_missing_lineage_id() -> None:
    fake = FakeRedisClient()
    store = RedisLineageStore(url="redis://x", client=fake)
    with pytest.raises(LineageNotFoundError):
        store.get("nope")


def test_list_ids_via_scan() -> None:
    fake = FakeRedisClient()
    store = RedisLineageStore(url="redis://x", client=fake)
    for jid in ("alpha", "beta", "gamma"):
        store.put(_step(jid, 0))
    ids = sorted(store.list_ids(limit=10))
    assert ids == ["alpha", "beta", "gamma"]


def test_serialized_record_carries_lineage_id() -> None:
    import json

    fake = FakeRedisClient()
    store = RedisLineageStore(url="redis://x", client=fake)
    store.put(_step("job-3", 0))
    body = fake.lists["lineage:job-3"][0]
    payload = json.loads(body)
    assert payload["lineage_id"] == "job-3"
    assert payload["step_index"] == 0
