"""Gemini Finance Support Center.

Responsibility split (enforced by construction, documented in code):

- GROQ            -> bounded per-case reconciliation AI investigator
                      (reconciliation/ai_controller.py) — reads one case.
- GEMINI          -> Finance Support Center (this module) — operator-facing
                      explanations, run summaries, cross-case patterns,
                      finance Q&A, review guidance.
- DETERMINISTIC   -> money calculation + financial validation + decision
                      authority (calculator/classifier/service).
- LEDGER          -> auditability + integrity (hash chain).

Gemini NEVER:
- computes or invents monetary values or metrics
- mutates payments/refunds/settlements/decisions/ledger state
- runs arbitrary SQL — it only ever sees bounded context that THIS module
  retrieves through tenant-scoped read-only functions
- fabricates IDs — every identifier the model may cite is taken from the
  deterministic context supplied here, and citations are validated

Failure isolation: any Gemini failure (missing key, HTTP 429/5xx, timeout,
connection error, malformed output) surfaces as a controlled support-center
error state.  It NEVER affects the deterministic reconciliation engine.

Usage metrics are real invocation counters (never fabricated): they count
actual calls made through this module per process.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured Gemini output schema
# ---------------------------------------------------------------------------

class SupportCenterAnswer(BaseModel):
    """Schema-validated Gemini Support Center answer.

    The model is instructed to ground every statement in the supplied
    deterministic context.  ``citations`` may only reference identifiers
    that exist in the context — the backend drops anything else.
    """

    answer: str = Field(description="Direct, evidence-grounded answer to the operator question")
    key_points: list[str] = Field(default_factory=list, description="Concise bullet points grounded in the data")
    citations: list[str] = Field(
        default_factory=list,
        description="case_id / run_id / payment_id references from the supplied context",
    )
    insufficient_evidence: bool = Field(
        default=False,
        description="True when the supplied context cannot support a confident answer",
    )


# ---------------------------------------------------------------------------
# Usage counters (real — incremented on actual calls)
# ---------------------------------------------------------------------------

class UsageCounters:
    """Per-process Gemini Support Center usage counters (real invocations)."""

    def __init__(self) -> None:
        self.invocations = 0
        self.failures = 0
        self.last_error = ""
        self.last_latency_ms: Optional[int] = None

    def record_success(self, latency_ms: int) -> None:
        self.invocations += 1
        self.last_latency_ms = latency_ms
        self.last_error = ""

    def record_failure(self, reason: str) -> None:
        self.failures += 1
        self.last_error = reason

    def snapshot(self) -> dict:
        return {
            "invocations": self.invocations,
            "failures": self.failures,
            "last_error": self.last_error,
            "last_latency_ms": self.last_latency_ms,
        }


_usage = UsageCounters()


def usage_snapshot() -> dict:
    return _usage.snapshot()


def reset_usage() -> None:
    """Reset usage counters (test isolation only)."""
    _usage.invocations = 0
    _usage.failures = 0
    _usage.last_error = ""
    _usage.last_latency_ms = None


# ---------------------------------------------------------------------------
# Read-only, tenant-scoped data services ("tools")
# ---------------------------------------------------------------------------
# Each function accepts an open DB connection + tenant_id and returns a
# bounded dict.  No function accepts a tenant_id from model output, no
# function writes, and all lookups are constrained to the tenant.

_MAX_LIST = 25


async def get_dashboard_metrics(db, tenant_id: str) -> dict:
    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM reconciliation_runs WHERE tenant_id = ?",
        (tenant_id,),
    )
    total_runs = (await cursor.fetchone())["cnt"]

    latest = None
    if total_runs:
        cursor = await db.execute(
            "SELECT * FROM reconciliation_runs WHERE tenant_id = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (tenant_id,),
        )
        row = await cursor.fetchone()
        if row:
            latest = _run_summary_dict(row, exception_histogram=None, tiers=None)

    cursor = await db.execute(
        "SELECT classification, COUNT(*) as cnt FROM reconciliation_cases "
        "WHERE tenant_id = ? GROUP BY classification",
        (tenant_id,),
    )
    counts = {r["classification"]: r["cnt"] for r in await cursor.fetchall()}
    total_cases = sum(counts.values())

    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM reconciliation_cases "
        "WHERE tenant_id = ? AND classification != 'MATCHED'",
        (tenant_id,),
    )
    unresolved = (await cursor.fetchone())["cnt"]

    cursor = await db.execute(
        "SELECT COALESCE(SUM(variance),0) as v FROM reconciliation_cases "
        "WHERE tenant_id = ?",
        (tenant_id,),
    )
    total_variance = (await cursor.fetchone())["v"]

    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM reconciliation_cases "
        "WHERE tenant_id = ? AND ai_invoked = 1",
        (tenant_id,),
    )
    ai_cases = (await cursor.fetchone())["cnt"]

    return {
        "total_runs": total_runs,
        "latest_run": latest,
        "total_cases": total_cases,
        "matched": counts.get("MATCHED", 0),
        "review_required": counts.get("REVIEW_REQUIRED", 0),
        "exceptions": counts.get("EXCEPTION", 0),
        "unresolved_exceptions": unresolved,
        "total_variance_paise": total_variance,
        "ai_invoked_cases": ai_cases,
        "ai_invocation_rate": (ai_cases / total_cases) if total_cases else 0.0,
    }


def _parse_json(val):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return {}
    return val or {}


def _run_summary_dict(row, exception_histogram, tiers) -> dict:
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "source": row["source"],
        "total_records": row["total_records"],
        "total_cases": row["total_cases"],
        "matched": row["matched"],
        "review_required": row["review_required"],
        "exceptions": row["exceptions"],
        "match_rate": row["match_rate"],
        "classification_accuracy": row["classification_accuracy"],
        "calculation_accuracy": row["calculation_accuracy"],
        "false_auto_resolve": row["false_auto_resolve"],
        "throughput_per_sec": row["throughput_per_sec"],
        "p50_latency_ms": row["p50_latency_ms"],
        "p95_latency_ms": row["p95_latency_ms"],
        "duplicates_detected": row["duplicates_detected"],
        "audit_completeness": row["audit_completeness"],
        "errors": _parse_json(row["errors"]) if "errors" in row.keys() else [],
        "started_at": row["started_at"] or "",
        "completed_at": row["completed_at"] or "",
        "exception_code_histogram": exception_histogram,
        "tier_counts": tiers,
    }


async def get_run_summary(db, tenant_id: str, run_id: str) -> dict:
    """Run-level metrics + exception-code histogram + tier counts."""
    cursor = await db.execute(
        "SELECT * FROM reconciliation_runs WHERE run_id = ? AND tenant_id = ?",
        (run_id, tenant_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise LookupError(f"run not found: {run_id}")

    # Exception-code histogram across the run's cases (deterministic).
    cursor = await db.execute(
        "SELECT exception_codes, tier_analysis FROM reconciliation_cases "
        "WHERE run_id = ? AND tenant_id = ?",
        (run_id, tenant_id),
    )
    hist: dict[str, int] = {}
    tier_counts: dict[str, int] = {}
    case_rows = await cursor.fetchall()
    for c in case_rows:
        for code in _parse_json(c["exception_codes"]):
            hist[str(code)] = hist.get(str(code), 0) + 1
        for t in _parse_json(c["tier_analysis"]).get("tiers_applied", []):
            key = str(t)
            tier_counts[key] = tier_counts.get(key, 0) + 1
    return _run_summary_dict(row, exception_histogram=hist, tiers=dict(sorted(tier_counts.items(), key=lambda kv: int(kv[0])) if tier_counts else tier_counts.items()))


async def get_exception_cases(db, tenant_id: str, run_id: str, limit: int = _MAX_LIST) -> dict:
    cursor = await db.execute(
        "SELECT * FROM reconciliation_cases WHERE run_id = ? AND tenant_id = ? "
        "AND classification != 'MATCHED' ORDER BY created_at DESC LIMIT ?",
        (run_id, tenant_id, limit),
    )
    rows = await cursor.fetchall()
    cases = [_case_summary_dict(r) for r in rows]
    return {"count": len(cases), "exception_cases": cases}


async def get_case(db, tenant_id: str, case_id: str) -> dict:
    cursor = await db.execute(
        "SELECT * FROM reconciliation_cases WHERE case_id = ? AND tenant_id = ?",
        (case_id, tenant_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise LookupError(f"case not found: {case_id}")
    return _case_full_dict(row)


async def get_case_decision(db, tenant_id: str, case_id: str) -> dict | None:
    """The ledger decision linked to a reconciliation case, if any."""
    cursor = await db.execute(
        "SELECT decision_id FROM reconciliation_cases WHERE case_id = ? AND tenant_id = ?",
        (case_id, tenant_id),
    )
    row = await cursor.fetchone()
    if not row or not row["decision_id"]:
        return None
    cursor = await db.execute(
        "SELECT decision_id, entity_type, entity_id, gross_amount, final_amount, "
        "status, decision_hash, created_at FROM decisions "
        "WHERE decision_id = ? AND tenant_id = ?",
        (row["decision_id"], tenant_id),
    )
    drow = await cursor.fetchone()
    if not drow:
        return None
    return {
        "decision_id": drow["decision_id"],
        "entity_type": drow["entity_type"],
        "entity_id": drow["entity_id"],
        "gross_amount_paise": drow["gross_amount"],
        "final_amount_paise": drow["final_amount"],
        "status": drow["status"],
        "decision_hash": drow["decision_hash"],
        "created_at": drow["created_at"] or "",
    }


async def get_related_cases(db, tenant_id: str, case_id: str, limit: int = 10) -> dict:
    """Cases sharing the same payment (duplicate runs / repeat evidence)."""
    cursor = await db.execute(
        "SELECT payment_id FROM reconciliation_cases WHERE case_id = ? AND tenant_id = ?",
        (case_id, tenant_id),
    )
    row = await cursor.fetchone()
    if not row:
        return {"count": 0, "related_cases": []}
    cursor = await db.execute(
        "SELECT * FROM reconciliation_cases WHERE tenant_id = ? "
        "AND payment_id = ? AND case_id != ? "
        "ORDER BY created_at DESC LIMIT ?",
        (tenant_id, row["payment_id"], case_id, limit),
    )
    rows = await cursor.fetchall()
    cases = [_case_summary_dict(r) for r in rows]
    return {"count": len(cases), "payment_id": row["payment_id"], "related_cases": cases}


async def search_cases(db, tenant_id: str, query: str, limit: int = _MAX_LIST) -> dict:
    q = f"%{query}%"
    cursor = await db.execute(
        "SELECT * FROM reconciliation_cases WHERE tenant_id = ? "
        "AND (payment_id LIKE ? OR case_id LIKE ? OR explanation LIKE ? OR "
        "exception_codes LIKE ?) ORDER BY created_at DESC LIMIT ?",
        (tenant_id, q, q, q, q, limit),
    )
    rows = await cursor.fetchall()
    cases = [_case_summary_dict(r) for r in rows]
    return {"count": len(cases), "cases": cases}


async def get_tier_summary(db, tenant_id: str, run_id: Optional[str] = None) -> dict:
    if run_id:
        cursor = await db.execute(
            "SELECT tier_analysis FROM reconciliation_cases "
            "WHERE tenant_id = ? AND run_id = ?",
            (tenant_id, run_id),
        )
    else:
        cursor = await db.execute(
            "SELECT tier_analysis FROM reconciliation_cases WHERE tenant_id = ?",
            (tenant_id,),
        )
    rows = await cursor.fetchall()
    tier_counts: dict[str, int] = {}
    for r in rows:
        for t in _parse_json(r["tier_analysis"]).get("tiers_applied", []):
            key = str(t)
            tier_counts[key] = tier_counts.get(key, 0) + 1
    ordered = dict(sorted(tier_counts.items(), key=lambda kv: int(kv[0])))
    return {"tier_counts": ordered, "run_id": run_id}


TIER_LABELS = {
    1: "Payment / Order",
    2: "Refund",
    3: "Settlement",
    4: "Fee / Tax",
    5: "Dispute / Risk",
    6: "Invoice / Payment Link",
    7: "Operational / Event Integrity",
}


def _case_summary_dict(row) -> dict:
    codes = _parse_json(row["exception_codes"])
    return {
        "case_id": row["case_id"],
        "payment_id": row["payment_id"],
        "run_id": row["run_id"],
        "classification": row["classification"],
        "exception_codes": codes,
        "variance_paise": row["variance"],
        "ai_invoked": bool(row["ai_invoked"]),
        "ai_status": row["ai_status"],
        "created_at": row["created_at"] or "",
    }


def _case_full_dict(row) -> dict:
    ta = _parse_json(row["tier_analysis"]) if "tier_analysis" in row.keys() else {}
    # Gather deterministic evidence refs actually attached to the case.
    evidence_refs: list[str] = []
    for exc in _parse_json(row["exceptions"]):
        for ref in exc.get("evidence_refs", []):
            if ref not in evidence_refs:
                evidence_refs.append(ref)
    for finding in ta.get("tier_findings", []):
        for ref in finding.get("evidence_refs", []):
            if ref not in evidence_refs:
                evidence_refs.append(ref)
    related = _parse_json(row["related_record_ids"])
    return {
        "case_id": row["case_id"],
        "payment_id": row["payment_id"],
        "run_id": row["run_id"],
        "classification": row["classification"],
        "expected_amount_paise": row["expected_amount"],
        "actual_amount_paise": row["actual_amount"],
        "variance_paise": row["variance"],
        "exception_codes": _parse_json(row["exception_codes"]),
        "exceptions": _parse_json(row["exceptions"]),
        "explanation": row["explanation"] or "",
        "calculation_trace": _parse_json(row["calculation_trace"]),
        "match_info": _parse_json(row["match_info"]),
        "related_record_ids": related,
        "evidence_refs": evidence_refs,
        "ai_invoked": bool(row["ai_invoked"]),
        "ai_status": row["ai_status"],
        "ai_trigger_reason": row["ai_trigger_reason"] or "",
        "ai_interpretation": _parse_json(row["ai_interpretation"]),
        "ai_technical_reason": row["ai_technical_reason"] or "",
        "tier_findings": ta.get("tier_findings", []),
        "tiers_applied": ta.get("tiers_applied", []),
        "relationships": ta.get("relationships", []),
        "decision_id": row["decision_id"] or "",
        "created_at": row["created_at"] or "",
    }


# ---------------------------------------------------------------------------
# Context assembly (bounded, deterministic-only)
# ---------------------------------------------------------------------------

SUPPORT_MODES = {
    "explain_exception": "Explain why a specific reconciliation case reached its classification",
    "summarize_run": "Summarize a reconciliation run from its real metrics",
    "pattern_analysis": "Identify recurring exception/pattern themes across cases",
    "review_assistant": "Recommend what an operator should investigate next",
    # 'finance_qa' and 'qa' are aliases for the same finance Q&A capability.
    "finance_qa": "Answer a finance-operations question from reconciliation data",
    "qa": "Answer a finance-operations question from reconciliation data (alias of finance_qa)",
}

# Modes whose behavior is identical (alias -> canonical).
_MODE_ALIASES = {"qa": "finance_qa"}


def canonical_mode(mode: str) -> str:
    """Resolve an alias to its canonical mode (unknown modes pass through)."""
    return _MODE_ALIASES.get(mode, mode)


def _trim_dict(value, max_chars: int = 6000) -> dict | str:
    """Bounded context: never forward unbounded record blobs to the model."""
    text = json.dumps(value, default=str)
    if len(text) <= max_chars:
        return value
    return text[:max_chars] + " ...[context truncated to keep prompt bounded]"


async def build_context(db, tenant_id: str, mode: str, question: str,
                        run_id: Optional[str] = None,
                        case_id: Optional[str] = None) -> tuple[dict, set[str]]:
    """Assemble the deterministic, tenant-scoped context for one question.

    Returns (context, allowed_ids).  ``allowed_ids`` is the set of real
    identifiers the model may cite; the answer validator enforces it.
    """
    mode = canonical_mode(mode)
    if mode not in SUPPORT_MODES:
        raise ValueError(f"Unsupported support mode '{mode}'")

    context: dict = {"mode": mode, "question": question}
    allowed: set[str] = set()

    if mode == "explain_exception":
        if not case_id:
            raise ValueError("explain_exception requires case_id")
        case = await get_case(db, tenant_id, case_id)
        decision = await get_case_decision(db, tenant_id, case_id)
        related = await get_related_cases(db, tenant_id, case_id, limit=5)
        context["case"] = _trim_dict(case)
        context["ledger_decision"] = decision
        context["related_cases"] = _trim_dict(related)
        # Every identifier the model may legitimately cite (they all appear
        # in the supplied deterministic context).
        allowed.add(case_id)
        allowed.add(case.get("run_id", ""))
        allowed.add(case.get("payment_id", ""))
        allowed.update(case.get("related_record_ids", []))
        allowed.update(case.get("evidence_refs", []))
        # Recursively collect identifier-like values actually present in the
        # deterministic case payload (relationship graph, tier finding detail
        # dicts, exception records) so legitimately cited IDs are never
        # stripped as fabricated.
        allowed.update(_collect_context_ids(case))
        for r in related.get("related_cases", []):
            allowed.add(r.get("case_id", ""))
            allowed.add(r.get("payment_id", ""))
        if decision:
            allowed.add(decision["decision_id"])

    elif mode == "summarize_run":
        if not run_id:
            raise ValueError("summarize_run requires run_id")
        summary = await get_run_summary(db, tenant_id, run_id)
        exceptions = await get_exception_cases(db, tenant_id, run_id, limit=10)
        tiers = await get_tier_summary(db, tenant_id, run_id)
        context["run_summary"] = _trim_dict(summary)
        context["sample_exception_cases"] = _trim_dict(exceptions)
        context["tier_summary"] = tiers
        allowed.add(run_id)
        for c in exceptions.get("exception_cases", []):
            allowed.add(c.get("case_id", ""))
            allowed.add(c.get("payment_id", ""))

    elif mode == "pattern_analysis":
        if run_id:
            summary = await get_run_summary(db, tenant_id, run_id)
            exceptions = await get_exception_cases(db, tenant_id, run_id, limit=_MAX_LIST)
            tiers = await get_tier_summary(db, tenant_id, run_id)
            allowed.add(run_id)
        else:
            dash = await get_dashboard_metrics(db, tenant_id)
            summary = dash.get("latest_run")
            # Latest run exception patterns when present.
            exceptions = {"count": 0, "exception_cases": []}
            tiers = {}
            if dash.get("latest_run"):
                exceptions = await get_exception_cases(
                    db, tenant_id, dash["latest_run"]["run_id"], limit=_MAX_LIST)
                tiers = await get_tier_summary(db, tenant_id, dash["latest_run"]["run_id"])
            context["dashboard"] = _trim_dict(dash)
        context["run_summary"] = _trim_dict(summary) if summary else None
        context["exception_cases"] = _trim_dict(exceptions)
        context["tier_summary"] = tiers
        for c in exceptions.get("exception_cases", []):
            allowed.add(c.get("case_id", ""))
            allowed.add(c.get("payment_id", ""))

    elif mode == "review_assistant":
        if case_id:
            case = await get_case(db, tenant_id, case_id)
            related = await get_related_cases(db, tenant_id, case_id, limit=5)
            context["case"] = _trim_dict(case)
            context["related_cases"] = _trim_dict(related)
            allowed.add(case_id)
            allowed.add(case.get("payment_id", ""))
            allowed.update(case.get("related_record_ids", []))
            allowed.update(case.get("evidence_refs", []))
            for r in related.get("related_cases", []):
                allowed.add(r.get("case_id", ""))
            run_id = case.get("run_id") or None
        dash = await get_dashboard_metrics(db, tenant_id)
        context["dashboard"] = _trim_dict(dash)
        if run_id:
            exceptions = await get_exception_cases(db, tenant_id, run_id, limit=_MAX_LIST)
            context["open_exceptions"] = _trim_dict(exceptions)
            allowed.add(run_id)

    else:  # finance_qa
        dash = await get_dashboard_metrics(db, tenant_id)
        context["dashboard"] = _trim_dict(dash)
        if run_id:
            summary = await get_run_summary(db, tenant_id, run_id)
            tiers = await get_tier_summary(db, tenant_id, run_id)
            context["run_summary"] = _trim_dict(summary)
            context["tier_summary"] = tiers
            allowed.add(run_id)
        elif dash.get("latest_run"):
            allowed.add(dash["latest_run"]["run_id"])

    # Never leave empty placeholders in the allowed set.
    allowed.discard("")
    return context, allowed


# ---------------------------------------------------------------------------
# Identifier collection (citation allow-list)
# ---------------------------------------------------------------------------

_ID_KEY_HINTS = ("id", "ids", "ref", "refs")
_ID_PREFIXES = (
    "pay_", "rcase_", "case_", "run_", "ref_", "set_", "disp_", "inv_",
    "plink_", "order_", "evt_", "rec_", "rzp_", "dec_", "decr_", "fee_",
    "tax_", "adj_", "acc_", "fa_",
)


def _collect_context_ids(payload) -> set[str]:
    """Collect identifier-like values from a deterministic case payload.

    Walks dicts/lists recursively.  A value counts as an identifier when its
    key looks like an id/ref field (ends in id/ids/ref/refs) or the value
    starts with a known EntitlementLedger id prefix.  This keeps the citation
    allow-list in sync with what the model can actually see in context, while
    fabricated/unknown strings never become citable.
    """
    found: set[str] = set()

    def _walk(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                _walk(v, k)
        elif isinstance(value, (list, tuple)):
            for item in value:
                _walk(item, "")
        elif isinstance(value, str) and value:
            low_key = key.lower()
            if any(h in low_key for h in _ID_KEY_HINTS) or value.startswith(_ID_PREFIXES):
                found.add(value)

    _walk(payload)
    return found


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _system_prompt() -> str:
    return (
        "You are the Finance Support Center inside a reconciliation system. "
        "All monetary values and metrics in the context were computed by a "
        "deterministic finance engine and are authoritative. You may ONLY "
        "reference data present in the supplied context. "
        "You MUST NOT invent, estimate, or recalculate amounts, percentages, "
        "counts, case IDs, run IDs, payment IDs, or any other fact. "
        "You are read-only advisory intelligence: you never approve, reject, "
        "or mutate financial state. "
        "If the context is insufficient to answer confidently, answer with "
        "what is actually known, set insufficient_evidence=true, and say the "
        "evidence is insufficient rather than guessing. "
        "Keep the answer concise and operator-focused. Citations may only be "
        "identifiers that literally appear in the context."
    )


# ---------------------------------------------------------------------------
# Gemini call + validation (bounded, structured)
# ---------------------------------------------------------------------------

@dataclass
class SupportCenterResult:
    status: str  # available | unavailable | failed
    answer: Optional[dict] = None
    technical_reason: str = ""
    latency_ms: int = 0
    provider: str = "gemini"
    model: str = ""
    unsupported_citations: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


def _classify_failure(exc: Exception) -> str:
    msg = str(exc)
    lower = msg.lower()
    if "429" in msg or "rate limit" in lower or "quota" in lower:
        return "Gemini API error 429: rate limit / quota exceeded"
    if "503" in msg:
        return "Gemini API error 503: service unavailable"
    if "401" in msg or "api_key" in lower or "unauthorized" in lower:
        return "Gemini authentication failed (invalid or missing API key)"
    if "timeout" in lower or "timed out" in lower or "deadline" in lower:
        return "Gemini request timed out"
    if "connection" in lower or "resolve" in lower or "unreachable" in lower:
        return "Gemini unavailable (connection error)"
    if "json" in lower or "schema" in lower or "parse" in lower or "validate" in lower:
        return "malformed Gemini output"
    return f"Gemini provider error: {type(exc).__name__}"


def _gemini_provider():
    """Default Gemini provider (env-configured)."""
    from ai.llm_provider import get_provider_by_name
    return get_provider_by_name("gemini")


def ask_support_center(
    question: str,
    context: dict,
    allowed_ids: set[str] | frozenset = frozenset(),
    provider=None,
    max_tokens: int = 8000,
) -> SupportCenterResult:
    """One bounded Gemini Support Center completion.

    - context is deterministic-only and tenant-scoped (assembled earlier).
    - provider is injectable for tests / failure simulation.
    - any provider or validation failure -> status failed/unavailable with
      a stable technical reason.  Never an invented answer.
    """
    start = time.time()

    if provider is None:
        try:
            provider = _gemini_provider()
        except Exception as e:  # key missing / SDK absent
            reason = _classify_failure(e)
            _usage.record_failure(reason)
            return SupportCenterResult(
                status="unavailable",
                technical_reason=reason,
                provider="gemini",
                usage=_usage.snapshot(),
            )

    try:
        p_info = provider.provider_info()
    except Exception:
        p_info = {}

    system = _system_prompt()
    contract = (
        "Return ONE JSON object with EXACTLY these keys and nothing else:\n"
        "{\"answer\": string, \"key_points\": array of strings, "
        "\"citations\": array of strings, \"insufficient_evidence\": boolean}\n"
        "- 'answer' is the direct operator-facing explanation.\n"
        "- 'key_points' are short bullet facts, all grounded in the context.\n"
        "- 'citations' may only contain identifiers (case_id/run_id/payment_id) "
        "that literally appear in the context.\n"
        "- 'insufficient_evidence' is true only when the context cannot support "
        "a confident answer.\n"
        "Do not add keys, prose outside the JSON, markdown fences or code blocks."
    )
    user = (
        f"Question from the finance operator:\n{question}\n\n"
        f"Deterministic context (authoritative, do not invent anything else):\n"
        f"{json.dumps(context, indent=2, default=str)}\n\n{contract}"
    )

    try:
        # JSON mode + strict backend schema validation.  Native structured
        # output is intentionally NOT requested here: Gemini rejects Pydantic
        # defaults in response schemas, and the Support Center already
        # validates every field + citation on our side (schema validation is
        # the safety boundary, not the provider's).
        raw = provider.complete_json(
            prompt=user,
            system=system,
            max_tokens=max_tokens,
            temperature=0.0,
        )
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        reason = _classify_failure(e)
        logger.warning("Support Center Gemini call failed (%s) after %dms", reason, latency_ms)
        _usage.record_failure(reason)
        return SupportCenterResult(
            status="failed" if "malformed" in reason else "unavailable",
            technical_reason=reason,
            latency_ms=latency_ms,
            provider=p_info.get("provider", "gemini"),
            model=p_info.get("model", ""),
            usage=_usage.snapshot(),
        )

    # ── Schema validation ──
    try:
        if isinstance(raw, dict):
            parsed = SupportCenterAnswer(**raw)
        elif hasattr(raw, "model_dump"):
            parsed = SupportCenterAnswer(**raw.model_dump())
        else:
            parsed = SupportCenterAnswer(**_coerce(raw))
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        reason = f"malformed Gemini output: {type(e).__name__}"
        logger.warning("Support Center schema validation failed: %s", reason)
        _usage.record_failure(reason)
        return SupportCenterResult(
            status="failed",
            technical_reason=reason,
            latency_ms=latency_ms,
            provider=p_info.get("provider", "gemini"),
            model=p_info.get("model", ""),
            usage=_usage.snapshot(),
        )

    # ── Citation validation: drop anything not in the supplied context ──
    allowed = set(allowed_ids)
    unsupported = [c for c in parsed.citations if c not in allowed]
    supported = [c for c in parsed.citations if c in allowed]
    parsed.citations = supported

    latency_ms = int((time.time() - start) * 1000)
    _usage.record_success(latency_ms)
    answer = parsed.model_dump()
    # Safety: never let the model override the real insufficient state if it
    # actually had evidence — but the flag stays advisory either way.
    return SupportCenterResult(
        status="available",
        answer=answer,
        latency_ms=latency_ms,
        provider=p_info.get("provider", "gemini"),
        model=p_info.get("model", ""),
        unsupported_citations=unsupported,
        usage=_usage.snapshot(),
    )


def _coerce(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    raise ValueError(f"cannot coerce {type(value).__name__} to dict")
