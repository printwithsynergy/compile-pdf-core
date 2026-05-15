"""S3-compatible retention store for opted-in producer inputs/outputs.

Public surface:

* :func:`persist_if_opted_in` — single call site every producer
  endpoint hits. Decides based on consent + env config whether to
  write anything, persists the three blobs, returns the decision so
  callers can stamp it onto the lineage record.
* :func:`delete_by_sha256` — backs the ``POST /v1/retention/delete``
  endpoint; bulk-deletes every object under the configured prefix
  whose key contains ``/{sha256}/``.

The boto3 client is constructed lazily so importing this module is
safe when retention isn't configured. Operators point at an
S3-compatible backend via :envvar:`COMPILE_RETAIN_ENDPOINT_URL`
(MinIO, R2, etc.).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any


class RetentionBackendError(RuntimeError):
    """Backend is configured but unreachable / misconfigured.

    Raised by the delete endpoint when boto3 fails (e.g. wrong
    credentials, bucket missing). ``persist_if_opted_in`` never
    raises — persistence failures are logged and treated as
    silent opt-outs so a downstream S3 hiccup doesn't fail a
    producer call.
    """


class RetentionStore:
    """Thin boto3 wrapper. Lazy client; reads config from env on first use."""

    def __init__(
        self,
        *,
        bucket: str | None = None,
        prefix: str | None = None,
        ttl_days: int | None = None,
        endpoint_url: str | None = None,
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._bucket = bucket if bucket is not None else _env("COMPILE_RETAIN_BUCKET")
        self._prefix = (
            prefix if prefix is not None else _env("COMPILE_RETAIN_PREFIX") or "retain"
        ).strip("/") or "retain"
        ttl_raw = _env("COMPILE_RETAIN_TTL_DAYS")
        self._ttl_days = ttl_days if ttl_days is not None else (int(ttl_raw) if ttl_raw else 90)
        self._endpoint_url = (
            endpoint_url if endpoint_url is not None else _env("COMPILE_RETAIN_ENDPOINT_URL")
        )
        self._region = region if region is not None else _env("COMPILE_RETAIN_REGION")
        self._client = client

    @property
    def bucket(self) -> str | None:
        return self._bucket or None

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def ttl_days(self) -> int:
        return self._ttl_days

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover — boto3 is a hard dep
            raise RetentionBackendError(f"boto3 unavailable: {exc}") from exc
        kwargs: dict[str, Any] = {}
        if self._endpoint_url:
            kwargs["endpoint_url"] = self._endpoint_url
        if self._region:
            kwargs["region_name"] = self._region
        ak = _env("COMPILE_RETAIN_AWS_ACCESS_KEY_ID")
        sk = _env("COMPILE_RETAIN_AWS_SECRET_ACCESS_KEY")
        if ak and sk:
            kwargs["aws_access_key_id"] = ak
            kwargs["aws_secret_access_key"] = sk
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def _key_root(self, *, tenant: str, producer: str, input_sha256: str) -> str:
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        return f"{self._prefix}/{tenant}/{producer}/{date}/{input_sha256}"

    def put_triplet(
        self,
        *,
        tenant: str,
        producer: str,
        input_sha256: str,
        input_bytes: bytes,
        output_bytes: bytes,
        result: dict[str, object],
    ) -> list[str]:
        """Write the three retention blobs and return their keys."""
        if not self._bucket:
            return []
        client = self._ensure_client()
        root = self._key_root(tenant=tenant, producer=producer, input_sha256=input_sha256)
        tagging = f"ttl-days={self._ttl_days}"
        keys = [f"{root}/input.pdf", f"{root}/output.pdf", f"{root}/result.json"]
        client.put_object(
            Bucket=self._bucket,
            Key=keys[0],
            Body=input_bytes,
            ContentType="application/pdf",
            Tagging=tagging,
        )
        client.put_object(
            Bucket=self._bucket,
            Key=keys[1],
            Body=output_bytes,
            ContentType="application/pdf",
            Tagging=tagging,
        )
        client.put_object(
            Bucket=self._bucket,
            Key=keys[2],
            Body=json.dumps(result, separators=(",", ":")).encode("utf-8"),
            ContentType="application/json",
            Tagging=tagging,
        )
        return keys

    def delete_matching(self, sha256: str) -> list[str]:
        """Delete every object whose key contains ``/{sha256}/``."""
        if not self._bucket:
            return []
        client = self._ensure_client()
        keys: list[str] = []
        marker = f"/{sha256}/"
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=f"{self._prefix}/"):
            for obj in page.get("Contents") or []:
                if marker in obj["Key"]:
                    keys.append(obj["Key"])
        if not keys:
            return []
        client.delete_objects(
            Bucket=self._bucket,
            Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True},
        )
        return keys


def persist_if_opted_in(
    *,
    consent: bool,
    producer: str,
    tenant: str,
    input_bytes: bytes,
    output_bytes: bytes,
    result: dict[str, object],
    input_sha256: str,
    store: RetentionStore | None = None,
) -> bool:
    """Persist the input/output/result triplet when consent + config align.

    Returns ``True`` if anything was written. Never raises — backend
    errors are swallowed and downgrade the call to a silent no-op so
    a transient S3 hiccup doesn't fail a producer request.

    ``result`` is mutated to strip ``output_pdf_b64`` before
    serialization (the bytes already live in ``output.pdf``).
    """
    if not consent:
        return False
    s = store if store is not None else RetentionStore()
    if not s.bucket:
        return False
    sanitized = {k: v for k, v in result.items() if k != "output_pdf_b64"}
    try:
        s.put_triplet(
            tenant=tenant,
            producer=producer,
            input_sha256=input_sha256,
            input_bytes=input_bytes,
            output_bytes=output_bytes,
            result=sanitized,
        )
    except Exception:
        return False
    return True


def delete_by_sha256(sha256: str, *, store: RetentionStore | None = None) -> list[str]:
    """Bulk-delete every retention object matching ``sha256``.

    Raises :class:`RetentionBackendError` when the backend is
    misconfigured (no bucket, boto3 unavailable, etc.) so the API
    surfaces it cleanly. ``boto3`` errors propagate as-is so the
    endpoint can map them to 5xx without guessing.
    """
    s = store if store is not None else RetentionStore()
    if not s.bucket:
        raise RetentionBackendError("COMPILE_RETAIN_BUCKET is not configured")
    return s.delete_matching(sha256)


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


__all__ = [
    "RetentionBackendError",
    "RetentionStore",
    "delete_by_sha256",
    "persist_if_opted_in",
]
