"""Celery task wrappers for the four producers + the CJD orchestrator.

Each task is a thin async transport over the existing in-process
engine entrypoint; the producer logic itself lives in
``compile_pdf.{producer}.engine``. Operators run a worker via:

.. code-block:: shell

    COMPILE_CELERY_BROKER_URL=redis://localhost/0 \\
    celery -A compile_pdf.tasks worker

…and submit jobs from the API layer (Phase 7c.x will add ``async=true``
flags on the producer endpoints) or from the CLI's planned
``--remote`` mode (Phase 7c.y).

Task payloads are JSON-friendly dicts: input PDFs are base64-encoded
in transit so the broker doesn't need binary support. Results follow
the same shape as the synchronous endpoints — ``{"output_pdf_b64",
"pdf_sha256", ...}`` for single-producer tasks; the CJD task adds
``lineage_id`` + ``steps``.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

from celery import Celery


def make_celery_app() -> Celery:
    """Build a Celery app from environment configuration.

    Constructed lazily so importing this module is safe even when no
    broker URL is configured. Reads:

    * ``COMPILE_CELERY_BROKER_URL`` — required for real workers; the
      default (``memory://``) lets tests run with
      ``task_always_eager=True``.
    * ``COMPILE_CELERY_RESULT_BACKEND`` — defaults to the broker URL.
    * ``COMPILE_CELERY_EAGER`` — when truthy, sets
      ``task_always_eager=True`` so submission resolves synchronously
      in the calling process. Used by the test suite.
    """
    broker = os.environ.get("COMPILE_CELERY_BROKER_URL", "memory://")
    eager = (os.environ.get("COMPILE_CELERY_EAGER") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # ``memory://`` works as a broker but Celery has no in-memory result
    # backend; eager mode returns results inline so the backend is moot.
    backend = os.environ.get("COMPILE_CELERY_RESULT_BACKEND")
    if backend is None and not (eager or broker.startswith("memory://")):
        backend = broker

    app = Celery("compile-pdf", broker=broker, backend=backend)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_always_eager=eager,
        task_eager_propagates=eager,
        broker_connection_retry_on_startup=False,
    )
    _register_tasks(app)
    return app


# --- Task definitions ---------------------------------------------------


def _register_tasks(app: Celery) -> None:
    """Attach the producer + CJD tasks to ``app``.

    The ``@app.task`` decorator comes from celery, which doesn't ship
    py.typed; mypy flags every decorated function as untyped. The
    per-line ignores keep the explicit return-type annotations in
    place for readers without poisoning the rest of the module.
    """

    @app.task(name="compile_pdf.rewrite.apply")  # type: ignore[untyped-decorator]
    def rewrite_apply(payload: dict[str, Any]) -> dict[str, Any]:
        from compile_pdf_core.rewrite.engine import apply_plan
        from compile_pdf_core.rewrite.plan_schema import RewritePlan

        input_bytes = base64.b64decode(payload["input_pdf_b64"], validate=True)
        plan = RewritePlan.model_validate(payload["plan"])
        result = apply_plan(input_bytes, plan)
        return {
            "output_pdf_b64": base64.b64encode(result.output_bytes).decode("ascii"),
            "pdf_sha256": result.pdf_sha256,
            "ops_applied": result.ops_applied,
        }

    @app.task(name="compile_pdf.marks.apply")  # type: ignore[untyped-decorator]
    def marks_apply(payload: dict[str, Any]) -> dict[str, Any]:
        from compile_pdf_core.marks.engine import apply_template
        from compile_pdf_core.marks.template_schema import MarksTemplate

        input_bytes = base64.b64decode(payload["input_pdf_b64"], validate=True)
        template = MarksTemplate.model_validate(payload["template"])
        result = apply_template(input_bytes, template)
        return {
            "output_pdf_b64": base64.b64encode(result.output_bytes).decode("ascii"),
            "pdf_sha256": result.pdf_sha256,
            "marks_applied": result.marks_applied,
        }

    @app.task(name="compile_pdf.impose.apply")  # type: ignore[untyped-decorator]
    def impose_apply(payload: dict[str, Any]) -> dict[str, Any]:
        from compile_pdf_core.impose.engine import apply_plan
        from compile_pdf_core.impose.layout_schema import ImposePlan

        input_bytes = base64.b64decode(payload["input_pdf_b64"], validate=True)
        plan = ImposePlan.model_validate(payload["plan"])
        result = apply_plan(input_bytes, plan)
        return {
            "output_pdf_b64": base64.b64encode(result.output_bytes).decode("ascii"),
            "pdf_sha256": result.pdf_sha256,
            "sheets_written": result.sheets_written,
            "cells_per_sheet": result.cells_per_sheet,
            "input_pages": result.input_pages,
        }

    @app.task(name="compile_pdf.trap.apply")  # type: ignore[untyped-decorator]
    def trap_apply(payload: dict[str, Any]) -> dict[str, Any]:
        from compile_pdf_core.trap.engine import apply_policy
        from compile_pdf_core.trap.policy_schema import TrapPolicy

        input_bytes = base64.b64decode(payload["input_pdf_b64"], validate=True)
        policy = TrapPolicy.model_validate(payload["policy"])
        result = apply_policy(input_bytes, policy)
        return {
            "output_pdf_b64": base64.b64encode(result.output_bytes).decode("ascii"),
            "pdf_sha256": result.pdf_sha256,
            "engine": result.engine,
            "engine_fingerprint": result.engine_fingerprint,
            "operations_count": len(result.operations),
            "trap_diff": result.trap_diff,
        }

    @app.task(name="compile_pdf.cjd.execute")  # type: ignore[untyped-decorator]
    def cjd_execute(job_payload: dict[str, Any]) -> dict[str, Any]:
        from compile_pdf_core.cjd.orchestrator import execute
        from compile_pdf_core.cjd.schema import CjdJob

        job = CjdJob.model_validate(job_payload)
        result = execute(job)
        return {
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
        }


# Module-level singleton consumed by the celery CLI: ``celery -A
# compile_pdf.tasks worker``. Constructed after ``_register_tasks`` is
# defined so the factory can attach tasks at import time.
celery_app = make_celery_app()


# --- Worker discovery ---------------------------------------------------


def detect_workers(*, timeout: float = 0.5) -> int:
    """Return the count of live Celery workers responding to ping.

    Returns 0 when no broker is configured, no workers are reachable,
    or any error occurs. Used by ``/v1/healthz.celery_workers`` to
    surface a "are there workers behind the broker?" signal without
    making the health check blocking or fragile.
    """
    if not (os.environ.get("COMPILE_CELERY_BROKER_URL") or "").strip():
        return 0
    try:
        replies = celery_app.control.inspect(timeout=timeout).ping()
    except Exception:
        return 0
    if not replies:
        return 0
    return len(replies)


def task_payload_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 over a JSON-serializable payload — exposed so the
    producer-task wrappers can attach an idempotency key for retries."""
    import json

    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


__all__ = [
    "celery_app",
    "detect_workers",
    "make_celery_app",
    "task_payload_hash",
]
