"""Compact analysis context builder.

The agent's Phase 1 (evidence gathering) can accumulate a large
conversation with many tool calls.  Phase 2 (final analysis) does
NOT need the full raw conversation — it needs:

  - Evidence IDs and their types
  - Tool results (summarized)
  - Agent's own findings
  - Policy IDs

This module builds a compact, bounded representation for Phase 2
that eliminates unnecessary token cost while preserving correctness.
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Hard limits for compact context
MAX_TOOL_RESULT_CHARS = 500
MAX_EVIDENCE_ITEMS = 30
MAX_FACTS = 50


def build_compact_analysis_context(
    conversation: list[dict],
    evidence_records: list[dict],
    agent_summary: str = "",
) -> str:
    """Build a compact analysis context from the agent's evidence gathering.

    Replaces the full conversation with a bounded summary that contains
    only the information needed for structured analysis.

    Args:
        conversation: Full agent conversation history
        evidence_records: Original evidence records
        agent_summary: The agent's summary from evidence gathering phase

    Returns:
        Compact string context for Phase 2 analysis
    """
    sections = []

    # Section 1: Evidence inventory (bounded)
    evidence_ids = []
    source_types = {}
    for ev in evidence_records[:MAX_EVIDENCE_ITEMS]:
        eid = ev.get("evidence_id", "")
        stype = ev.get("source_type", "")
        evidence_ids.append(eid)
        source_types[eid] = stype

    sections.append(
        f"Evidence inventory ({len(evidence_ids)} records):\n"
        + "\n".join(f"  {eid}: {source_types.get(eid, 'unknown')}" for eid in evidence_ids)
    )

    # Section 2: Tool results (summarized)
    tool_results = []
    seen_evidence = set()
    for msg in conversation:
        if msg.get("role") == "tool":
            name = msg.get("name", "unknown")
            try:
                content = json.loads(msg.get("content", "{}"))
                # Extract key facts from tool results
                summary = _summarize_tool_result(name, content)
                if summary:
                    tool_results.append(f"Tool [{name}]: {summary}")
                    # Track evidence IDs
                    _extract_evidence_ids(content, seen_evidence)
            except (json.JSONDecodeError, TypeError):
                pass

    if tool_results:
        sections.append(
            f"Tool results ({len(tool_results)} calls):\n"
            + "\n".join(f"  {r}" for r in tool_results[:20])
        )

    # Section 3: Evidence IDs discovered via tools
    if seen_evidence:
        sections.append(
            f"Additional evidence discovered: {', '.join(sorted(seen_evidence))}"
        )

    # Section 4: Agent's summary from evidence gathering
    if agent_summary:
        sections.append(f"Agent findings:\n  {agent_summary[:1000]}")

    return "\n\n".join(sections)


def _summarize_tool_result(tool_name: str, content: dict) -> Optional[str]:
    """Extract a compact summary from a tool result."""
    if not content.get("found", False):
        return None

    if tool_name == "get_order":
        return (
            f"Order {content.get('order_id', '?')}: "
            f"amount={content.get('amount', '?')}, "
            f"status={content.get('status', '?')}"
        )
    elif tool_name == "get_payment":
        payments = content.get("payments", [])
        if payments:
            return f"{len(payments)} payment(s) found"
        return (
            f"Payment {content.get('payment_id', '?')}: "
            f"status={content.get('status', '?')}"
        )
    elif tool_name == "get_refund":
        refunds = content.get("refunds", [])
        if refunds:
            return f"{len(refunds)} refund(s): " + ", ".join(
                f"{r.get('refund_id', '?')} ({r.get('status', '?')})"
                for r in refunds[:3]
            )
    elif tool_name == "get_delivery":
        deliveries = content.get("deliveries", [])
        if deliveries:
            d = deliveries[0]
            return (
                f"Delivery: promised={d.get('promised_date', '?')}, "
                f"actual={d.get('actual_date', '?')}, "
                f"delay={d.get('delay_days', '?')} days"
            )
    elif tool_name == "get_return":
        returns = content.get("returns", [])
        if returns:
            return f"{len(returns)} return record(s)"
    elif tool_name == "get_policy":
        return (
            f"Policy {content.get('policy_id', '?')}: "
            f"v{content.get('version', '?')}"
        )
    elif tool_name == "search_evidence":
        evidence = content.get("evidence", [])
        if evidence:
            return f"{len(evidence)} evidence record(s) of type {evidence[0].get('source_type', '?')}"
    elif tool_name == "get_invoice":
        invoices = content.get("invoices", [])
        if invoices:
            return f"{len(invoices)} invoice(s)"

    return None


def _extract_evidence_ids(content: dict, seen: set):
    """Extract evidence IDs from a tool result into the seen set."""
    if "evidence_id" in content:
        seen.add(content["evidence_id"])

    for key in ("evidence", "refunds", "deliveries", "returns", "payments"):
        items = content.get(key, [])
        if isinstance(items, list):
            for item in items:
                eid = item.get("evidence_id")
                if eid:
                    seen.add(eid)
