"""Shared FastAPI router for polling async job status.

Mounted at ``/v1/jobs`` from :mod:`compile_pdf.api.main`. Provides a
single endpoint:

* ``GET /v1/jobs/{job_id}`` — poll job status; returns 404 when
  the job is unknown or has expired (24h TTL).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from compile_pdf_core.async_jobs import AsyncJobStatus, get_job

router = APIRouter()


@router.get(
    "/{job_id}",
    response_model=AsyncJobStatus,
    responses={
        404: {"description": "Job not found or expired"},
    },
)
async def get_job_status(job_id: str) -> AsyncJobStatus:
    """Poll async job status.

    Returns the current state of an async job submitted via ``?async=true``.
    Poll until status is ``complete`` or ``failed``. Jobs expire after 24h.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id!r} not found",
        )
    return AsyncJobStatus(**job)


__all__ = ["router"]
