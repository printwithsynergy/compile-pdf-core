"""Celery task wrappers — eager-mode round-trip + worker detection."""

from __future__ import annotations

import base64
import io
import os

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name


@pytest.fixture(autouse=True)
def _eager_celery(monkeypatch: pytest.MonkeyPatch):
    """All tests run in CELERY_TASK_ALWAYS_EAGER mode.

    Forces submission to resolve synchronously so we don't need a live
    broker. We rebuild ``celery_app`` per test so each one observes the
    current env (e.g. tests that flip ``COMPILE_CELERY_BROKER_URL``).
    """
    monkeypatch.setenv("COMPILE_CELERY_EAGER", "true")
    yield


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _printer_pdf() -> bytes:
    pdf = pikepdf.new()
    pdf.pages.append(
        pikepdf.Page(
            pdf.make_indirect(
                Dictionary(
                    Type=Name.Page,
                    MediaBox=Array([0, 0, 612, 792]),
                    TrimBox=Array([36, 36, 576, 756]),
                    BleedBox=Array([18, 18, 594, 774]),
                    Resources=Dictionary(),
                    Contents=pdf.make_stream(b"q 100 100 m 200 200 l S Q"),
                )
            )
        )
    )
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True, linearize=False)
    pdf.close()
    return buf.getvalue()


def test_rewrite_task_round_trips() -> None:
    from compile_pdf_core.tasks import make_celery_app

    app = make_celery_app()
    task = app.tasks["compile_pdf.rewrite.apply"]
    result = task.apply(
        kwargs={
            "payload": {
                "input_pdf_b64": _b64(_printer_pdf()),
                "plan": {"ops": [{"op": "metadata_set", "key": "Title", "value": "via celery"}]},
            }
        }
    )
    body = result.get()
    assert body["ops_applied"] == 1
    assert body["pdf_sha256"]


def test_marks_task_round_trips() -> None:
    from compile_pdf_core.tasks import make_celery_app

    app = make_celery_app()
    task = app.tasks["compile_pdf.marks.apply"]
    body = task.apply(
        kwargs={
            "payload": {
                "input_pdf_b64": _b64(_printer_pdf()),
                "template": {"marks": [{"type": "register", "anchor": "trim_corners"}]},
            }
        }
    ).get()
    assert body["marks_applied"] == 4  # broadcast


def test_impose_task_round_trips() -> None:
    from compile_pdf_core.tasks import make_celery_app

    app = make_celery_app()
    task = app.tasks["compile_pdf.impose.apply"]
    body = task.apply(
        kwargs={
            "payload": {
                "input_pdf_b64": _b64(_printer_pdf()),
                "plan": {
                    "sheet": {"width_pt": 612, "height_pt": 792},
                    "cell": {"width_pt": 612, "height_pt": 792},
                },
            }
        }
    ).get()
    assert body["sheets_written"] == 1
    assert body["cells_per_sheet"] == 1


def test_trap_task_returns_diff() -> None:
    from compile_pdf_core.tasks import make_celery_app

    app = make_celery_app()
    task = app.tasks["compile_pdf.trap.apply"]
    body = task.apply(
        kwargs={
            "payload": {
                "input_pdf_b64": _b64(_printer_pdf()),
                "policy": {
                    "trap_zones": [
                        {
                            "page_index": 0,
                            "rect_pt": [50, 50, 100, 100],
                            "from_ink": "Y",
                            "to_ink": "K",
                        }
                    ]
                },
            }
        }
    ).get()
    assert body["operations_count"] == 1
    assert body["trap_diff"]["operations"][0]["from_ink"] == "Y"


def test_cjd_task_orchestrates_full_chain() -> None:
    from compile_pdf_core.tasks import make_celery_app

    app = make_celery_app()
    task = app.tasks["compile_pdf.cjd.execute"]
    body = task.apply(
        kwargs={
            "job_payload": {
                "input_pdf_b64": _b64(_printer_pdf()),
                "steps": [
                    {"type": "rewrite", "plan": {"ops": []}},
                    {
                        "type": "marks",
                        "template": {"marks": [{"type": "register", "anchor": "trim_corners"}]},
                    },
                ],
            }
        }
    ).get()
    assert body["lineage_id"]
    assert [s["producer"] for s in body["steps"]] == ["rewrite", "marks"]


def test_detect_workers_returns_zero_without_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPILE_CELERY_BROKER_URL", raising=False)
    from compile_pdf import tasks as tasks_module

    assert tasks_module.detect_workers() == 0


def test_detect_workers_returns_zero_when_broker_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPILE_CELERY_BROKER_URL", "redis://127.0.0.1:1/0")
    # detect_workers uses the module-level celery_app, so patch its
    # control.inspect to simulate a broker timeout.
    from compile_pdf import tasks as tasks_module

    class FakeInspect:
        def ping(self) -> None:
            raise OSError("broker unreachable")

    class FakeControl:
        def inspect(self, *, timeout: float) -> FakeInspect:
            _ = timeout
            return FakeInspect()

    monkeypatch.setattr(tasks_module.celery_app, "control", FakeControl())
    assert tasks_module.detect_workers() == 0


def test_detect_workers_counts_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_CELERY_BROKER_URL", "redis://x")
    from compile_pdf import tasks as tasks_module

    class FakeInspect:
        def ping(self) -> dict[str, dict[str, str]]:
            return {"worker-a": {"ok": "pong"}, "worker-b": {"ok": "pong"}}

    class FakeControl:
        def inspect(self, *, timeout: float) -> FakeInspect:
            _ = timeout
            return FakeInspect()

    monkeypatch.setattr(tasks_module.celery_app, "control", FakeControl())
    assert tasks_module.detect_workers() == 2


def test_payload_hash_is_stable() -> None:
    from compile_pdf_core.tasks import task_payload_hash

    a = task_payload_hash({"a": 1, "b": [1, 2, 3]})
    b = task_payload_hash({"b": [1, 2, 3], "a": 1})
    assert a == b


def test_make_celery_app_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_CELERY_BROKER_URL", "redis://example/0")
    monkeypatch.setenv("COMPILE_CELERY_RESULT_BACKEND", "redis://example/1")
    from compile_pdf_core.tasks import make_celery_app

    app = make_celery_app()
    assert app.conf.broker_url == "redis://example/0"
    assert app.conf.result_backend == "redis://example/1"


# Module-level guard so pytest doesn't error if celery exits 0 silently
_ = os
