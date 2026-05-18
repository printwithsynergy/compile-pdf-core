"""Lightweight async job store backed by Redis.

Used by ?async=true endpoints for impose, trap, and CJD.
Job state is stored in Redis with 24h TTL (jobs are short-lived;
results should be retrieved and stored by the caller promptly).
"""

from __future__ import annotations

import json
import os
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    complete = "complete"
    failed = "failed"


def _redis_url() -> str:
    """Resolve the Redis URL for job storage.

    Prefers ``COMPILE_CELERY_BROKER_URL`` when it is a real Redis URL,
    then falls back to ``COMPILE_LINEAGE_REDIS_URL``, then to localhost.
    ``memory://`` (the Celery eager-mode stub URL) is intentionally
    skipped so the job store always uses a real Redis instance.
    """
    for env in ("COMPILE_CELERY_BROKER_URL", "COMPILE_LINEAGE_REDIS_URL"):
        val = (os.environ.get(env) or "").strip()
        if val and val.startswith(("redis://", "rediss://", "unix://")):
            return val
    return "redis://localhost:6379/0"


def _redis_client():  # type: ignore[no-untyped-def]
    import redis

    return redis.from_url(_redis_url(), decode_responses=True)


JOB_TTL = 86_400  # 24 hours


def create_job(kind: str, payload_hash: str) -> str:
    """Create a new pending job. Returns job_id."""
    job_id = str(uuid.uuid4())
    data: dict[str, Any] = {
        "job_id": job_id,
        "kind": kind,
        "status": JobStatus.pending,
        "payload_hash": payload_hash,
        "result": None,
        "error": None,
    }
    r = _redis_client()
    r.setex(f"compile:job:{job_id}", JOB_TTL, json.dumps(data))
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    """Retrieve job state from Redis. Returns None if not found."""
    r = _redis_client()
    raw = r.get(f"compile:job:{job_id}")
    if raw is None:
        return None
    return json.loads(raw)  # type: ignore[no-any-return]


def update_job(
    job_id: str,
    status: JobStatus,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Update job status (and optionally result/error) in Redis."""
    r = _redis_client()
    key = f"compile:job:{job_id}"
    raw = r.get(key)
    if raw is None:
        return
    data: dict[str, Any] = json.loads(raw)
    data["status"] = status
    if result is not None:
        data["result"] = result
    if error is not None:
        data["error"] = error
    r.setex(key, JOB_TTL, json.dumps(data))


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class AsyncJobAccepted(BaseModel):
    """202 response for accepted async jobs."""

    job_id: str
    status: str = "pending"
    poll_url: str  # e.g. /v1/jobs/{job_id}


class AsyncJobStatus(BaseModel):
    """GET /v1/jobs/{job_id} response."""

    job_id: str
    kind: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None


__all__ = [
    "AsyncJobAccepted",
    "AsyncJobStatus",
    "JOB_TTL",
    "JobStatus",
    "create_job",
    "get_job",
    "update_job",
]
