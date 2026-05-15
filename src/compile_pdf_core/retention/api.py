"""FastAPI router for retention erasure requests.

Single endpoint: ``POST /v1/retention/delete``. Bearer-protected via
the router-level ``Depends(authenticate)`` already mounted in
:mod:`compile_pdf.api.main`. Honours data-subject erasure requests
by walking the bucket and deleting every object whose key contains
``/{sha256}/``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from compile_pdf_core.retention.store import RetentionBackendError, delete_by_sha256

logger = structlog.get_logger(__name__)

router = APIRouter()


class RetentionDeleteRequest(BaseModel):
    model_config = {"extra": "forbid"}

    sha256: str = Field(min_length=1, max_length=128)


class RetentionDeleteResponse(BaseModel):
    model_config = {"extra": "forbid"}

    deleted: int
    keys: list[str]


@router.post(
    "/delete",
    response_model=RetentionDeleteResponse,
    status_code=status.HTTP_200_OK,
)
async def retention_delete(payload: RetentionDeleteRequest) -> RetentionDeleteResponse:
    """Delete every retention object whose key contains the given sha256.

    Always returns 200 with a count and the deleted keys; zero hits
    is not an error (matches the spec: "any matching key", which may
    be none). Misconfiguration (no bucket) → 503; boto3 errors → 500.
    """
    try:
        keys = delete_by_sha256(payload.sha256)
    except RetentionBackendError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("retention.delete.error", sha256=payload.sha256[:16], err=str(exc))
        raise HTTPException(status_code=500, detail=f"retention delete failed: {exc}") from exc

    logger.info("retention.delete.ok", sha256=payload.sha256[:16], deleted=len(keys))
    return RetentionDeleteResponse(deleted=len(keys), keys=keys)
