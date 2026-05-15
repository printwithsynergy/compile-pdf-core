"""Lineage record store — abstract interface plus three backends.

Per spec §1.6a + §4.5.2 a CJD job emits one lineage record per producer
step, keyed by ``lineage_id`` and ordered by ``step_index``. Three
backends ship today:

* **memory** (default) — process-local dict, non-durable. Appropriate
  for development and the in-process test suite.
* **s3** — boto3-driven, one JSON object per step under
  ``{prefix}/{lineage_id}/{step_index:04d}.json``. Selects via
  ``COMPILE_LINEAGE_BACKEND=s3`` plus ``COMPILE_LINEAGE_S3_BUCKET``.
* **redis** — redis-py-driven, one ``RPUSH`` per step into the list
  ``lineage:{lineage_id}``. Selects via
  ``COMPILE_LINEAGE_BACKEND=redis`` plus ``COMPILE_LINEAGE_REDIS_URL``.

Both durable backends serialize records via :func:`_serialize_step` and
deserialize via :func:`_deserialize_step`, so the on-disk shape is the
same as ``GET /v1/lineage/{id}`` returns.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LineageStep:
    """One per-producer step record. Persistence-layer agnostic.

    ``trap_diff`` is populated only for trap steps (auto-emitted per
    spec §5.7). Other producers leave it ``None``.
    """

    lineage_id: str
    step_index: int
    producer: str
    input_sha256: str
    output_sha256: str
    cache_key: str
    plan_sha256: str
    extras: dict[str, object] = field(default_factory=dict)
    trap_diff: dict[str, object] | None = None
    retained_for_training: bool = False


@dataclass(frozen=True)
class LineageChain:
    """All lineage records for a single ``lineage_id``, ordered by step."""

    lineage_id: str
    steps: tuple[LineageStep, ...]

    def step(self, index: int) -> LineageStep:
        return self.steps[index]


class LineageStore(Protocol):
    """Persistence interface for lineage records."""

    def put(self, record: LineageStep) -> None: ...
    def get(self, lineage_id: str) -> LineageChain: ...
    def list_ids(self, *, limit: int = 50) -> list[str]: ...


class LineageNotFoundError(KeyError):
    """The requested lineage_id has no records in this store."""


class LineageBackendError(RuntimeError):
    """Backend is misconfigured (missing env, unreachable host, etc.)."""


# --- Memory backend -----------------------------------------------------


class MemoryLineageStore:
    """In-process dict-backed store. Default for v1.

    Thread-safe (orchestrator may run multi-step jobs on a worker pool
    in a future revision; the lock keeps insertions atomic per lineage_id).
    """

    def __init__(self) -> None:
        self._data: dict[str, list[LineageStep]] = {}
        self._lock = threading.Lock()

    def put(self, record: LineageStep) -> None:
        with self._lock:
            chain = self._data.setdefault(record.lineage_id, [])
            chain.append(record)
            chain.sort(key=lambda s: s.step_index)

    def get(self, lineage_id: str) -> LineageChain:
        with self._lock:
            steps = self._data.get(lineage_id)
            if steps is None:
                raise LineageNotFoundError(lineage_id)
            return LineageChain(lineage_id=lineage_id, steps=tuple(steps))

    def list_ids(self, *, limit: int = 50) -> list[str]:
        with self._lock:
            return list(self._data.keys())[:limit]

    def clear(self) -> None:
        """Test-only helper. Production stores never expose this."""
        with self._lock:
            self._data.clear()


# --- S3 backend ---------------------------------------------------------


class S3LineageStore:
    """S3-backed store. One JSON object per step.

    Object key shape: ``{prefix}/{lineage_id}/{step_index:04d}.json``.
    Listing scans the bucket prefix; for high-throughput operators a
    secondary index (Redis or DynamoDB) is recommended. The object body
    is the same shape as :func:`_serialize_step` returns, plus a
    ``lineage_id`` field for round-trip parity.

    The boto3 client is constructed lazily so importing this module
    works even when ``COMPILE_LINEAGE_BACKEND != "s3"``.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "lineage",
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise LineageBackendError("S3 lineage backend requires a bucket name")
        self._bucket = bucket
        self._prefix = prefix.strip("/") or "lineage"
        self._client = client  # lazy: defer boto3 import

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover — boto3 is a hard dep
            raise LineageBackendError(f"boto3 unavailable: {exc}") from exc
        self._client = boto3.client("s3")
        return self._client

    def _key_for(self, lineage_id: str, step_index: int) -> str:
        return f"{self._prefix}/{lineage_id}/{step_index:04d}.json"

    def put(self, record: LineageStep) -> None:
        body = json.dumps(_serialize_step_with_id(record), separators=(",", ":")).encode("utf-8")
        self._ensure_client().put_object(
            Bucket=self._bucket,
            Key=self._key_for(record.lineage_id, record.step_index),
            Body=body,
            ContentType="application/json",
        )

    def get(self, lineage_id: str) -> LineageChain:
        client = self._ensure_client()
        prefix = f"{self._prefix}/{lineage_id}/"
        response = client.list_objects_v2(Bucket=self._bucket, Prefix=prefix)
        contents = response.get("Contents") or []
        if not contents:
            raise LineageNotFoundError(lineage_id)
        steps: list[LineageStep] = []
        for obj in contents:
            key = obj["Key"]
            body = client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
            steps.append(_deserialize_step(json.loads(body), lineage_id=lineage_id))
        steps.sort(key=lambda s: s.step_index)
        return LineageChain(lineage_id=lineage_id, steps=tuple(steps))

    def list_ids(self, *, limit: int = 50) -> list[str]:
        client = self._ensure_client()
        prefix = f"{self._prefix}/"
        response = client.list_objects_v2(Bucket=self._bucket, Prefix=prefix, Delimiter="/")
        ids: list[str] = []
        for entry in response.get("CommonPrefixes") or []:
            sub = entry.get("Prefix", "")
            if not sub.startswith(prefix):
                continue
            tail = sub[len(prefix) :].rstrip("/")
            if tail:
                ids.append(tail)
            if len(ids) >= limit:
                break
        return ids


# --- Redis backend ------------------------------------------------------


class RedisLineageStore:
    """Redis-backed store. List per ``lineage_id``.

    Each ``put`` is an ``RPUSH`` of the JSON-serialized step onto
    ``lineage:{lineage_id}``. ``get`` reads via ``LRANGE`` and re-sorts
    by ``step_index`` defensively. ``list_ids`` uses ``SCAN`` against
    the ``lineage:*`` pattern to avoid blocking the server on
    large keyspaces.

    The redis client is constructed lazily and the URL parsed from
    ``COMPILE_LINEAGE_REDIS_URL`` (or the explicit ``url`` arg) so
    importing this module is safe when redis isn't reachable.
    """

    def __init__(self, *, url: str, namespace: str = "lineage", client: Any | None = None) -> None:
        if not url:
            raise LineageBackendError("Redis lineage backend requires a URL")
        self._url = url
        self._namespace = namespace.strip(":") or "lineage"
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import redis
        except ImportError as exc:  # pragma: no cover — redis is a hard dep
            raise LineageBackendError(f"redis-py unavailable: {exc}") from exc
        self._client = redis.from_url(self._url)
        return self._client

    def _key_for(self, lineage_id: str) -> str:
        return f"{self._namespace}:{lineage_id}"

    def put(self, record: LineageStep) -> None:
        body = json.dumps(_serialize_step_with_id(record), separators=(",", ":"))
        self._ensure_client().rpush(self._key_for(record.lineage_id), body)

    def get(self, lineage_id: str) -> LineageChain:
        client = self._ensure_client()
        raw = client.lrange(self._key_for(lineage_id), 0, -1)
        if not raw:
            raise LineageNotFoundError(lineage_id)
        steps: list[LineageStep] = []
        for item in raw:
            body = item.decode("utf-8") if isinstance(item, bytes) else item
            steps.append(_deserialize_step(json.loads(body), lineage_id=lineage_id))
        steps.sort(key=lambda s: s.step_index)
        return LineageChain(lineage_id=lineage_id, steps=tuple(steps))

    def list_ids(self, *, limit: int = 50) -> list[str]:
        client = self._ensure_client()
        pattern = f"{self._namespace}:*"
        ids: list[str] = []
        for key in client.scan_iter(match=pattern, count=limit):
            decoded = key.decode("utf-8") if isinstance(key, bytes) else key
            tail = decoded[len(self._namespace) + 1 :]
            if tail:
                ids.append(tail)
            if len(ids) >= limit:
                break
        return ids


# --- Backend selection --------------------------------------------------


_DEFAULT_STORE: MemoryLineageStore = MemoryLineageStore()


def default_store() -> MemoryLineageStore:
    """Process-wide singleton for the in-memory store. The API + CLI
    both read/write through this so a CJD job's lineage is visible to
    a subsequent ``GET /v1/lineage/{id}`` in the same process."""
    return _DEFAULT_STORE


def reset_default_store() -> None:
    """Test-only helper — clears the singleton between test runs."""
    _DEFAULT_STORE.clear()


def select_store(backend: str | None = None) -> LineageStore:
    """Resolve a backend name to a store instance.

    When ``backend`` is ``None`` the value of
    ``COMPILE_LINEAGE_BACKEND`` is consulted (default ``memory``).
    """
    name = (backend or os.environ.get("COMPILE_LINEAGE_BACKEND") or "memory").strip().lower()
    if name == "memory":
        return _DEFAULT_STORE
    if name == "s3":
        return S3LineageStore(
            bucket=os.environ.get("COMPILE_LINEAGE_S3_BUCKET", ""),
            prefix=os.environ.get("COMPILE_LINEAGE_S3_PREFIX", "lineage"),
        )
    if name == "redis":
        return RedisLineageStore(
            url=os.environ.get("COMPILE_LINEAGE_REDIS_URL", ""),
            namespace=os.environ.get("COMPILE_LINEAGE_REDIS_NAMESPACE", "lineage"),
        )
    raise LineageBackendError(
        f"unknown lineage backend {name!r}; expected one of memory | s3 | redis"
    )


def serialize_chain(chain: LineageChain) -> dict[str, object]:
    """Render a chain as a JSON-friendly dict."""
    return {
        "lineage_id": chain.lineage_id,
        "steps": [_serialize_step(step) for step in chain.steps],
    }


def _serialize_step(step: LineageStep) -> dict[str, object]:
    payload: dict[str, object] = {
        "step_index": step.step_index,
        "producer": step.producer,
        "input_sha256": step.input_sha256,
        "output_sha256": step.output_sha256,
        "cache_key": step.cache_key,
        "plan_sha256": step.plan_sha256,
        "retained_for_training": step.retained_for_training,
    }
    if step.extras:
        payload["extras"] = dict(step.extras)
    if step.trap_diff is not None:
        payload["trap_diff"] = step.trap_diff
    return payload


def _serialize_step_with_id(step: LineageStep) -> dict[str, object]:
    """Same shape as :func:`_serialize_step` plus ``lineage_id`` so a
    durable backend's stored record round-trips without context."""
    payload = _serialize_step(step)
    payload["lineage_id"] = step.lineage_id
    return payload


def _deserialize_step(payload: dict[str, Any], *, lineage_id: str) -> LineageStep:
    return LineageStep(
        lineage_id=payload.get("lineage_id", lineage_id),
        step_index=int(payload["step_index"]),
        producer=str(payload["producer"]),
        input_sha256=str(payload["input_sha256"]),
        output_sha256=str(payload["output_sha256"]),
        cache_key=str(payload["cache_key"]),
        plan_sha256=str(payload["plan_sha256"]),
        extras=dict(payload.get("extras") or {}),
        trap_diff=payload.get("trap_diff"),
        retained_for_training=bool(payload.get("retained_for_training", False)),
    )


def serialize_steps(steps: Iterable[LineageStep]) -> list[dict[str, object]]:
    return [_serialize_step(s) for s in steps]


__all__ = [
    "LineageBackendError",
    "LineageChain",
    "LineageNotFoundError",
    "LineageStep",
    "LineageStore",
    "MemoryLineageStore",
    "RedisLineageStore",
    "S3LineageStore",
    "default_store",
    "reset_default_store",
    "select_store",
    "serialize_chain",
    "serialize_steps",
]
