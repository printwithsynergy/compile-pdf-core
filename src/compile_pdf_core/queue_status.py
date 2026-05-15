"""Queue-depth resolver consumed by ``GET /v1/healthz``.

Three backends:

* ``none`` (default) — always returns ``0``. Appropriate for in-process
  deployments where there is no queue.
* ``celery`` — uses the Celery ``inspect`` API to count
  reserved + active tasks across all workers. Selected via
  ``COMPILE_QUEUE_BACKEND=celery``; broker URL is read from
  ``COMPILE_CELERY_BROKER_URL``. If the broker is unreachable or no
  workers are present, the resolver returns ``0`` rather than raising —
  health checks must not fail when the queue is merely empty.
* ``redis`` — counts entries in a Redis list keyed by
  ``COMPILE_QUEUE_REDIS_KEY`` (default ``compile:queue``). Useful when
  the broker is Redis-backed and Celery isn't installed.

The resolver is *advisory*. ``/v1/healthz`` does not gate on it; the
value surfaces operational signal rather than liveness.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any


def resolve_queue_depth() -> int:
    """Read the configured backend and return current queue depth.

    Never raises — any backend error returns ``0`` so liveness probes
    keep succeeding.
    """
    backend = (os.environ.get("COMPILE_QUEUE_BACKEND") or "none").strip().lower()
    if backend in {"", "none"}:
        return 0
    try:
        if backend == "celery":
            return _celery_depth()
        if backend == "redis":
            return _redis_depth()
    except Exception:
        return 0
    return 0


def _celery_depth() -> int:
    try:
        from celery import Celery
    except ImportError:  # pragma: no cover — celery is a hard dep
        return 0
    broker = os.environ.get("COMPILE_CELERY_BROKER_URL", "")
    if not broker:
        return 0
    app = Celery("compile-pdf", broker=broker)
    try:
        inspect = app.control.inspect(timeout=0.5)
        reserved = inspect.reserved() or {}
        active = inspect.active() or {}
    except Exception:
        return 0
    finally:
        with contextlib.suppress(Exception):  # pragma: no cover — defensive close
            app.close()
    total = 0
    for worker_tasks in reserved.values():
        total += len(worker_tasks or [])
    for worker_tasks in active.values():
        total += len(worker_tasks or [])
    return total


def _redis_depth() -> int:
    url = os.environ.get("COMPILE_CELERY_BROKER_URL") or os.environ.get(
        "COMPILE_LINEAGE_REDIS_URL", ""
    )
    key = os.environ.get("COMPILE_QUEUE_REDIS_KEY", "compile:queue")
    if not url:
        return 0
    try:
        import redis
    except ImportError:  # pragma: no cover — redis is a hard dep
        return 0
    client: Any = redis.from_url(url)
    try:
        depth = client.llen(key)
    except Exception:
        return 0
    return int(depth or 0)


__all__ = [
    "resolve_queue_depth",
]
