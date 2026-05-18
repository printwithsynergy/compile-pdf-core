"""Wrapper Celery tasks that update the async job store.

Each wrapper:

1. Marks the job ``running`` at the start.
2. Calls the underlying engine function directly (same logic as the
   main task, but with job-store bookkeeping).
3. Marks the job ``complete`` on success or ``failed`` on error.

These tasks are registered on the module-level ``celery_app`` singleton
from :mod:`compile_pdf_core.tasks` and are intended to be used by the
``?async=true`` API endpoints.
"""

from __future__ import annotations

import base64
from typing import Any

from compile_pdf_core.async_jobs import JobStatus, update_job
from compile_pdf_core.tasks import celery_app


@celery_app.task(name="compile_pdf.async_wrap.trap")  # type: ignore[misc]
def async_wrap_trap(job_id: str, payload: dict[str, Any]) -> None:
    """Run the trap engine and update job store with the result."""
    update_job(job_id, JobStatus.running)
    try:
        from compile_pdf_trap.engine import apply_policy
        from compile_pdf_trap.policy_schema import TrapPolicy

        input_bytes = base64.b64decode(payload["input_pdf_b64"], validate=True)
        policy = TrapPolicy.model_validate(payload["policy"])
        result = apply_policy(input_bytes, policy)
        update_job(
            job_id,
            JobStatus.complete,
            result={
                "output_pdf_b64": base64.b64encode(result.output_bytes).decode("ascii"),
                "pdf_sha256": result.pdf_sha256,
                "engine": result.engine,
                "engine_fingerprint": result.engine_fingerprint,
                "operations_count": len(result.operations),
                "trap_diff": result.trap_diff,
            },
        )
    except Exception as exc:
        update_job(job_id, JobStatus.failed, error=str(exc))
        raise


@celery_app.task(name="compile_pdf.async_wrap.impose")  # type: ignore[misc]
def async_wrap_impose(job_id: str, payload: dict[str, Any]) -> None:
    """Run the impose engine and update job store with the result."""
    update_job(job_id, JobStatus.running)
    try:
        from compile_pdf_impose.engine import apply_plan
        from compile_pdf_impose.layout_schema import ImposePlan

        input_bytes = base64.b64decode(payload["input_pdf_b64"], validate=True)
        plan = ImposePlan.model_validate(payload["plan"])
        result = apply_plan(input_bytes, plan)
        update_job(
            job_id,
            JobStatus.complete,
            result={
                "output_pdf_b64": base64.b64encode(result.output_bytes).decode("ascii"),
                "pdf_sha256": result.pdf_sha256,
                "sheets_written": result.sheets_written,
                "cells_per_sheet": result.cells_per_sheet,
                "input_pages": result.input_pages,
            },
        )
    except Exception as exc:
        update_job(job_id, JobStatus.failed, error=str(exc))
        raise


@celery_app.task(name="compile_pdf.async_wrap.cjd")  # type: ignore[misc]
def async_wrap_cjd(job_id: str, job_payload: dict[str, Any]) -> None:
    """Run the CJD orchestrator and update job store with the result."""
    update_job(job_id, JobStatus.running)
    try:
        from compile_pdf_cjd.orchestrator import execute
        from compile_pdf_cjd.schema import CjdJob

        job = CjdJob.model_validate(job_payload)
        result = execute(job)
        update_job(
            job_id,
            JobStatus.complete,
            result={
                "output_pdf_b64": base64.b64encode(result.output_pdf_bytes).decode("ascii"),
                "output_pdf_sha256": result.output_pdf_sha256,
                "lineage_id": result.lineage_id,
                "steps": [
                    {
                        "step_index": s.step_index,
                        "producer": s.producer,
                        "input_sha256": s.input_sha256,
                        "output_sha256": s.output_sha256,
                        "cache_key": s.cache_key,
                    }
                    for s in result.steps
                ],
                "trap_diff": result.trap_diff,
            },
        )
    except Exception as exc:
        update_job(job_id, JobStatus.failed, error=str(exc))
        raise


__all__ = [
    "async_wrap_cjd",
    "async_wrap_impose",
    "async_wrap_trap",
]
