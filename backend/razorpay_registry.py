"""Canonical Razorpay event registry — one classification layer for all events.

Every Razorpay event type is classified into a family with explicit
metadata describing how the application should treat it:

- financial_relevance: does this event carry a monetary amount that can
  become financial evidence?
- creates_evidence: should this event project into the evidence layer?
- affects_reconciliation: can this event change reconciliation inputs?
- context_risk_only: is this operational/risk context that must NOT
  silently modify money calculations?
- ai_useful: could AI interpretation add value for this event family
  (semantic correlation / root-cause), or is deterministic handling
  always sufficient?
- normalization_strategy: how the event is normalized into records.

The registry is authoritative and central — routes, adapters, and the
event serializer derive classification from it instead of scattering
hard-coded event lists.  It never guesses payload schemas: only event
types that map to a known family with a defined strategy are treated as
known; everything else is classified 'unknown' and handled
conservatively (stored, never silently treated as financial truth).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Event families
# ---------------------------------------------------------------------------

FAMILY_PAYMENT = "PAYMENT"
FAMILY_ORDER = "ORDER"
FAMILY_REFUND = "REFUND"
FAMILY_SETTLEMENT = "SETTLEMENT"
FAMILY_DISPUTE = "DISPUTE"
FAMILY_DOWNTIME = "DOWNTIME"
FAMILY_INVOICE = "INVOICE"
FAMILY_PAYMENT_LINK = "PAYMENT_LINK"
FAMILY_ACCOUNT = "ACCOUNT"
FAMILY_FUND_ACCOUNT = "FUND_ACCOUNT"
FAMILY_ENGAGEMENT = "ENGAGEMENT"
FAMILY_UNKNOWN = "UNKNOWN"

ALL_FAMILIES = {
    FAMILY_PAYMENT, FAMILY_ORDER, FAMILY_REFUND, FAMILY_SETTLEMENT,
    FAMILY_DISPUTE, FAMILY_DOWNTIME, FAMILY_INVOICE, FAMILY_PAYMENT_LINK,
    FAMILY_ACCOUNT, FAMILY_FUND_ACCOUNT, FAMILY_ENGAGEMENT, FAMILY_UNKNOWN,
}

# ---------------------------------------------------------------------------
# Known event types → family
# ---------------------------------------------------------------------------

EVENT_FAMILY_MAP: dict[str, str] = {
    # PAYMENT
    "payment.authorized": FAMILY_PAYMENT,
    "payment.captured": FAMILY_PAYMENT,
    "payment.failed": FAMILY_PAYMENT,
    "payment.pending": FAMILY_PAYMENT,
    "payment.pending_v2": FAMILY_PAYMENT,
    "payment.captured_async": FAMILY_PAYMENT,
    "payment.held": FAMILY_PAYMENT,
    "payment.resumed": FAMILY_PAYMENT,
    "payment.cancelled": FAMILY_PAYMENT,
    # ORDER
    "order.paid": FAMILY_ORDER,
    "order.notification.failure": FAMILY_ORDER,
    "order.notification.insufficient_funds": FAMILY_ORDER,
    # REFUND
    "refund.created": FAMILY_REFUND,
    "refund.processed": FAMILY_REFUND,
    "refund.failed": FAMILY_REFUND,
    "refund.speed_changed": FAMILY_REFUND,
    # SETTLEMENT
    "settlement.processed": FAMILY_SETTLEMENT,
    "settlement.pending": FAMILY_SETTLEMENT,
    "settlement.reversed": FAMILY_SETTLEMENT,
    # DISPUTE (risk context — never silently alters money)
    "payment.dispute.created": FAMILY_DISPUTE,
    "payment.dispute.accepted": FAMILY_DISPUTE,
    "payment.dispute.rejected": FAMILY_DISPUTE,
    "payment.dispute.won": FAMILY_DISPUTE,
    "payment.dispute.lost": FAMILY_DISPUTE,
    "payment.dispute.closed": FAMILY_DISPUTE,
    "payment.dispute.under_review": FAMILY_DISPUTE,
    "payment.dispute.action_required": FAMILY_DISPUTE,
    # DOWNTIME (operational context only)
    "payment.downtime.started": FAMILY_DOWNTIME,
    "payment.downtime.resumed": FAMILY_DOWNTIME,
    "payment.downtime.resolved": FAMILY_DOWNTIME,
    # INVOICE
    "invoice.paid": FAMILY_INVOICE,
    "invoice.partially_paid": FAMILY_INVOICE,
    "invoice.issued": FAMILY_INVOICE,
    "invoice.expired": FAMILY_INVOICE,
    # PAYMENT LINK
    "payment_link.paid": FAMILY_PAYMENT_LINK,
    "payment_link.partially_paid": FAMILY_PAYMENT_LINK,
    "payment_link.cancelled": FAMILY_PAYMENT_LINK,
    "payment_link.expired": FAMILY_PAYMENT_LINK,
    # ACCOUNT / FUND ACCOUNT
    "account.created": FAMILY_ACCOUNT,
    "account.updated": FAMILY_ACCOUNT,
    "account.activated": FAMILY_ACCOUNT,
    "account.deactivated": FAMILY_ACCOUNT,
    "fund_account.created": FAMILY_FUND_ACCOUNT,
    "fund_account.updated": FAMILY_FUND_ACCOUNT,
    # ENGAGEMENT
    "engage.review.rejected": FAMILY_ENGAGEMENT,
    "engage.review.approved": FAMILY_ENGAGEMENT,
    "engage.review.created": FAMILY_ENGAGEMENT,
}

# ---------------------------------------------------------------------------
# Family metadata
# ---------------------------------------------------------------------------

_FAMILY_META: dict[str, dict] = {
    FAMILY_PAYMENT: {
        "financial_relevance": True,
        "creates_evidence": True,
        "affects_reconciliation": True,
        "context_risk_only": False,
        "ai_useful": False,
        "normalization_strategy": "payment → reconciliation payment record",
    },
    FAMILY_ORDER: {
        "financial_relevance": True,
        "creates_evidence": True,
        "affects_reconciliation": True,
        "context_risk_only": False,
        "ai_useful": False,
        "normalization_strategy": "order → order reference for payment linkage",
    },
    FAMILY_REFUND: {
        "financial_relevance": True,
        "creates_evidence": True,
        "affects_reconciliation": True,
        "context_risk_only": False,
        "ai_useful": False,
        "normalization_strategy": "refund → refund record (deducted from expected settlement)",
    },
    FAMILY_SETTLEMENT: {
        "financial_relevance": True,
        "creates_evidence": True,
        "affects_reconciliation": True,
        "context_risk_only": False,
        "ai_useful": False,
        "normalization_strategy": "settlement → settlement record (actual settlement comparison)",
    },
    FAMILY_DISPUTE: {
        "financial_relevance": False,
        "creates_evidence": True,
        "affects_reconciliation": False,
        "context_risk_only": True,
        "ai_useful": True,
        "normalization_strategy": "risk/context evidence — may inform review, never silently changes money",
    },
    FAMILY_DOWNTIME: {
        "financial_relevance": False,
        "creates_evidence": True,
        "affects_reconciliation": False,
        "context_risk_only": True,
        "ai_useful": False,
        "normalization_strategy": "operational context evidence — no monetary effect",
    },
    FAMILY_INVOICE: {
        "financial_relevance": True,
        "creates_evidence": True,
        "affects_reconciliation": False,
        "context_risk_only": False,
        "ai_useful": False,
        "normalization_strategy": "invoice evidence — financial but outside payment reconciliation",
    },
    FAMILY_PAYMENT_LINK: {
        "financial_relevance": True,
        "creates_evidence": True,
        "affects_reconciliation": False,
        "context_risk_only": False,
        "ai_useful": False,
        "normalization_strategy": "payment-link evidence — financial, correlates via payment",
    },
    FAMILY_ACCOUNT: {
        "financial_relevance": False,
        "creates_evidence": False,
        "affects_reconciliation": False,
        "context_risk_only": True,
        "ai_useful": False,
        "normalization_strategy": "account lifecycle context — no reconciliation effect",
    },
    FAMILY_FUND_ACCOUNT: {
        "financial_relevance": False,
        "creates_evidence": False,
        "affects_reconciliation": False,
        "context_risk_only": True,
        "ai_useful": False,
        "normalization_strategy": "fund-account lifecycle context — no reconciliation effect",
    },
    FAMILY_ENGAGEMENT: {
        "financial_relevance": False,
        "creates_evidence": False,
        "affects_reconciliation": False,
        "context_risk_only": True,
        "ai_useful": False,
        "normalization_strategy": "engagement/review context — no reconciliation effect",
    },
    FAMILY_UNKNOWN: {
        "financial_relevance": False,
        "creates_evidence": True,
        "affects_reconciliation": False,
        "context_risk_only": True,
        "ai_useful": False,
        "normalization_strategy": "unknown event — stored as evidence, never treated as financial truth",
    },
}


def classify_event(event_type: str) -> dict:
    """Classify a Razorpay event type into its canonical family metadata.

    Returns a dict with: event_type, family, known, and the family's
    financial_relevance / creates_evidence / affects_reconciliation /
    context_risk_only / ai_useful / normalization_strategy flags.
    """
    event_type = (event_type or "").strip()
    family = EVENT_FAMILY_MAP.get(event_type, FAMILY_UNKNOWN)
    meta = _FAMILY_META[family]
    return {
        "event_type": event_type,
        "family": family,
        "known": family != FAMILY_UNKNOWN,
        "financial_relevance": meta["financial_relevance"],
        "creates_evidence": meta["creates_evidence"],
        "affects_reconciliation": meta["affects_reconciliation"],
        "context_risk_only": meta["context_risk_only"],
        "ai_useful": meta["ai_useful"],
        "normalization_strategy": meta["normalization_strategy"],
    }


def is_known(event_type: str) -> bool:
    """Whether the event type maps to a known family (not UNKNOWN)."""
    return classify_event(event_type)["known"]


def is_financially_relevant(event_type: str) -> bool:
    """Whether the event carries a monetary amount relevant to reconciliation."""
    return classify_event(event_type)["financial_relevance"]


def affects_reconciliation(event_type: str) -> bool:
    """Whether the event can change reconciliation inputs."""
    return classify_event(event_type)["affects_reconciliation"]


def family_label(family: str) -> str:
    """Human-readable label for a family (fallback: raw family)."""
    return {
        FAMILY_PAYMENT: "Payment",
        FAMILY_ORDER: "Order",
        FAMILY_REFUND: "Refund",
        FAMILY_SETTLEMENT: "Settlement",
        FAMILY_DISPUTE: "Dispute",
        FAMILY_DOWNTIME: "Downtime",
        FAMILY_INVOICE: "Invoice",
        FAMILY_PAYMENT_LINK: "Payment Link",
        FAMILY_ACCOUNT: "Account",
        FAMILY_FUND_ACCOUNT: "Fund Account",
        FAMILY_ENGAGEMENT: "Engagement",
        FAMILY_UNKNOWN: "Unknown",
    }.get(family, family)


# ---------------------------------------------------------------------------
# Event family → reconciliation tier (the canonical dispatch map)
# ---------------------------------------------------------------------------
# Tier assignment is a property of the family, not of individual event
# types: every member of a family participates in the same reconciliation
# domain.  This is the single source used by the registry-driven dispatcher.

FAMILY_TIER: dict[str, int] = {
    FAMILY_PAYMENT: 1,
    FAMILY_ORDER: 1,
    FAMILY_REFUND: 2,
    FAMILY_SETTLEMENT: 3,
    FAMILY_DISPUTE: 5,
    FAMILY_INVOICE: 6,
    FAMILY_PAYMENT_LINK: 6,
    FAMILY_DOWNTIME: 7,
    FAMILY_ACCOUNT: 7,
    FAMILY_FUND_ACCOUNT: 7,
    FAMILY_ENGAGEMENT: 7,
    FAMILY_UNKNOWN: 7,
}

# Family → reconciliation record type projected for the engine.  Financially
# relevant families map to the five financial record types the calculator
# consumes; risk/operational families map to context record types that the
# engine carries as evidence without touching settlement arithmetic.
FAMILY_RECORD_TYPE: dict[str, str] = {
    FAMILY_PAYMENT: "payment",
    FAMILY_ORDER: "payment",  # order evidence rides on the payment record
    FAMILY_REFUND: "refund",
    FAMILY_SETTLEMENT: "settlement",
    FAMILY_DISPUTE: "dispute",
    FAMILY_INVOICE: "invoice",
    FAMILY_PAYMENT_LINK: "payment_link",
    FAMILY_DOWNTIME: "operational",
    FAMILY_ACCOUNT: "operational",
    FAMILY_FUND_ACCOUNT: "operational",
    FAMILY_ENGAGEMENT: "operational",
    FAMILY_UNKNOWN: "operational",
}

# Dispute lifecycle: which dispute event types leave the dispute OPEN
# (risk active) vs CLOSED (resolved).  Unknown dispute types stay open
# conservatively.
DISPUTE_OPEN_EVENTS = {
    "payment.dispute.created",
    "payment.dispute.under_review",
    "payment.dispute.action_required",
    "payment.dispute.accepted",  # accepted for processing — still open
}
DISPUTE_CLOSED_EVENTS = {
    "payment.dispute.won",
    "payment.dispute.lost",
    "payment.dispute.closed",
    "payment.dispute.rejected",
}


def event_tier(event_type: str) -> int:
    """Reconciliation tier an event participates in (canonical dispatch)."""
    return FAMILY_TIER[classify_event(event_type)["family"]]


def event_record_type(event_type: str) -> str:
    """Engine record type an event projects to."""
    return FAMILY_RECORD_TYPE[classify_event(event_type)["family"]]


def dispute_event_is_open(event_type: str) -> bool:
    """Whether a dispute lifecycle event represents an OPEN dispute.

    Conservative default: an unlisted dispute event is treated as open so
    risk is never silently dropped.
    """
    if event_type in DISPUTE_CLOSED_EVENTS:
        return False
    if event_type in DISPUTE_OPEN_EVENTS:
        return True
    if event_type.startswith("payment.dispute."):
        return True
    return False