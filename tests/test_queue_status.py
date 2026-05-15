"""Queue-depth resolver — backend selection + graceful failure."""

from __future__ import annotations

import pytest

from compile_pdf import queue_status


def test_default_backend_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPILE_QUEUE_BACKEND", raising=False)
    assert queue_status.resolve_queue_depth() == 0


def test_explicit_none_backend_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_QUEUE_BACKEND", "none")
    assert queue_status.resolve_queue_depth() == 0


def test_unknown_backend_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_QUEUE_BACKEND", "rabbit")
    assert queue_status.resolve_queue_depth() == 0


def test_celery_backend_without_broker_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPILE_QUEUE_BACKEND", "celery")
    monkeypatch.delenv("COMPILE_CELERY_BROKER_URL", raising=False)
    assert queue_status.resolve_queue_depth() == 0


def test_celery_backend_with_unreachable_broker_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting an unreachable broker should not raise; resolver swallows
    the timeout and returns 0 so liveness probes keep working."""
    monkeypatch.setenv("COMPILE_QUEUE_BACKEND", "celery")
    monkeypatch.setenv("COMPILE_CELERY_BROKER_URL", "redis://127.0.0.1:1/0")
    assert queue_status.resolve_queue_depth() == 0


def test_redis_backend_without_url_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPILE_QUEUE_BACKEND", "redis")
    monkeypatch.delenv("COMPILE_CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("COMPILE_LINEAGE_REDIS_URL", raising=False)
    assert queue_status.resolve_queue_depth() == 0


def test_celery_backend_counts_reserved_plus_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the Celery class so we can simulate reserved/active payloads
    without actually running a broker."""
    monkeypatch.setenv("COMPILE_QUEUE_BACKEND", "celery")
    monkeypatch.setenv("COMPILE_CELERY_BROKER_URL", "redis://x")

    class FakeInspect:
        def reserved(self) -> dict[str, list[dict[str, str]]]:
            return {"worker-a": [{"id": "1"}, {"id": "2"}]}

        def active(self) -> dict[str, list[dict[str, str]]]:
            return {"worker-a": [{"id": "3"}]}

    class FakeControl:
        def inspect(self, *, timeout: float) -> FakeInspect:
            _ = timeout
            return FakeInspect()

    class FakeCelery:
        def __init__(self, name: str, *, broker: str) -> None:
            _ = name, broker
            self.control = FakeControl()

        def close(self) -> None:
            pass

    import celery as celery_module

    monkeypatch.setattr(celery_module, "Celery", FakeCelery)
    assert queue_status.resolve_queue_depth() == 3
