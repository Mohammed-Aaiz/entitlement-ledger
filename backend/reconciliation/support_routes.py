"""Finance Support Center API routes.

    POST /api/reconciliation/support/ask      — Gemini-grounded support answer
    GET  /api/reconciliation/support/status  — provider + usage status
    GET  /api/reconciliation/support/modes   — supported capabilities

All endpoints are tenant-scoped and authenticated.  Gemini failures surface
as controlled error envelopes (AI_UNAVAILABLE) and never affect the
deterministic reconciliation engine.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import CurrentUser, get_current_user
from database import get_db

from . import support_center
from .support_center import (
    SUPPORT_MODES,
    ask_support_center,
    build_context,
    usage_snapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class SupportAskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    mode: str = "finance_qa"
    run_id: str = ""
    case_id: str = ""


class SupportStatusResponse(BaseModel):
    provider: str = "gemini"
    available: bool = False
    model: str = ""
    error: str = ""
    modes: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)


class SupportAskResponse(BaseModel):
    status: str
    mode: str
    answer: dict = Field(default_factory=dict)
    technical_reason: str = ""
    latency_ms: int = 0
    provider: str = ""
    model: str = ""
    usage: dict = Field(default_factory=dict)


def _provider_status() -> dict:
    """Real Gemini availability from the configured provider."""
    try:
        from ai.llm_provider import get_provider_by_name
        prov = get_provider_by_name("gemini")
        info = prov.provider_info()
        available = bool(prov.is_available())
        return {"provider": "gemini", "available": available,
                "model": info.get("model", "") if available else "",
                "error": "" if available else "GEMINI_API_KEY not set or Gemini SDK unavailable"}
    except Exception as e:
        msg = str(e)
        return {"provider": "gemini", "available": False, "model": "",
                "error": msg or "Gemini unavailable"}


@router.get("/reconciliation/support/status", response_model=SupportStatusResponse)
async def support_status(user: CurrentUser = Depends(get_current_user)):
    status = _provider_status()
    return SupportStatusResponse(
        **status,
        modes=SUPPORT_MODES,
        usage=usage_snapshot(),
    )


@router.get("/reconciliation/support/modes")
async def support_modes(user: CurrentUser = Depends(get_current_user)):
    return {"modes": SUPPORT_MODES}


@router.post("/reconciliation/support/ask", response_model=SupportAskResponse)
async def support_ask(req: SupportAskRequest, user: CurrentUser = Depends(get_current_user)):
    """Ask the Gemini Finance Support Center a grounded question."""
    mode = support_center.canonical_mode(req.mode)
    if mode not in SUPPORT_MODES:
        raise HTTPException(422, f"Unsupported support mode '{req.mode}'. Valid: {sorted(SUPPORT_MODES)}")

    db = await get_db()
    try:
        try:
            context, allowed_ids = await build_context(
                db, user.tenant_id, mode, req.question,
                run_id=req.run_id or None, case_id=req.case_id or None,
            )
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

        # Dry-run sanity: every scope id must belong to this tenant — already
        # enforced inside build_context by tenant-scoped queries.
        result = ask_support_center(req.question, context, allowed_ids=allowed_ids)

        if result.status != "available":
            # Friendly provider-unavailable/failed state — never presented as
            # an answer.  Transient failures get a retryable 503; malformed
            # output stays non-retryable (502).
            if result.status == "unavailable":
                raise HTTPException(
                    503,
                    detail=result.technical_reason or "Gemini unavailable",
                    headers={"Retry-After": "5"},
                )
            raise HTTPException(
                502,
                detail=result.technical_reason or "Gemini failed",
            )

        return SupportAskResponse(
            status="ok",
            mode=mode,
            answer=result.answer or {},
            latency_ms=result.latency_ms,
            provider=result.provider,
            model=result.model,
            usage=result.usage,
        )
    finally:
        await db.close()
