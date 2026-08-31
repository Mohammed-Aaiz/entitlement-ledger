"""Bounded tools for the Finance Controller Agent.

Each tool queries the existing database/evidence layer or Razorpay-derived data.
No fake external integrations. No creation of financial records.
All tools are read-only and tenant-scoped.

The LLM determines WHAT evidence is needed; tools retrieve it.
Tools never calculate monetary amounts or approve/reject payments.
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas — JSON definitions for the LLM prompt
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "get_order",
        "description": "Retrieve order details by order ID. Returns order amount, status, seller, and dates.",
        "parameters": {
            "order_id": "string — the order ID to look up"
        },
    },
    {
        "name": "get_payment",
        "description": "Retrieve payment details by payment ID or order ID. Returns amount, status, method, and timestamps.",
        "parameters": {
            "payment_id": "string — optional payment ID",
            "order_id": "string — optional order ID to find payments for"
        },
    },
    {
        "name": "get_refund",
        "description": "Retrieve refund records for an order. Returns refund amounts, reasons, and processing status.",
        "parameters": {
            "order_id": "string — the order ID to look up refunds for"
        },
    },
    {
        "name": "get_settlement",
        "description": "Retrieve settlement records for an order. Returns settlement amounts, status, and timestamps.",
        "parameters": {
            "settlement_id": "string — optional settlement ID",
            "order_id": "string — optional order ID to find settlements for"
        },
    },
    {
        "name": "get_delivery",
        "description": "Retrieve delivery records from evidence. Returns promised/actual dates, delay info, and carrier.",
        "parameters": {
            "order_id": "string — the order ID to look up delivery for"
        },
    },
    {
        "name": "get_return",
        "description": "Retrieve return/reserve records from evidence. Returns return amounts, reasons, and processing status.",
        "parameters": {
            "order_id": "string — the order ID to look up returns for"
        },
    },
    {
        "name": "get_invoice",
        "description": "Retrieve invoice data from evidence. Returns invoice amounts and line items.",
        "parameters": {
            "order_id": "string — the order ID to look up invoice for"
        },
    },
    {
        "name": "get_policy",
        "description": "Retrieve a specific policy clause by policy ID. Returns the full clause text, version, and effective date.",
        "parameters": {
            "policy_id": "string — the policy ID to look up"
        },
    },
    {
        "name": "search_evidence",
        "description": "Search for evidence records by source type within the tenant. Returns evidence IDs and summaries.",
        "parameters": {
            "source_type": "string — evidence type: order, delivery, complaint, refund_record, payment, invoice"
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations — query the existing database
# ---------------------------------------------------------------------------

async def tool_get_order(tenant_id: str, order_id: str) -> dict:
    """Retrieve order details from Razorpay orders or evidence records."""
    from database import get_db

    db = await get_db()
    try:
        # Try Razorpay orders first
        cursor = await db.execute(
            "SELECT order_id, entity_id, amount, currency, status, receipt, notes, raw_payload "
            "FROM razorpay_orders WHERE order_id = ? AND tenant_id = ?",
            (order_id, tenant_id),
        )
        row = await cursor.fetchone()
        if row:
            return {
                "found": True,
                "source": "razorpay_orders",
                "order_id": row["order_id"],
                "entity_id": row["entity_id"],
                "amount": row["amount"],
                "currency": row["currency"],
                "status": row["status"],
                "receipt": row["receipt"],
            }

        # Fallback: search evidence for order data
        cursor = await db.execute(
            "SELECT evidence_id, source_type, raw_content FROM evidence "
            "WHERE tenant_id = ? AND source_type = 'order'",
            (tenant_id,),
        )
        rows = await cursor.fetchall()
        for ev in rows:
            try:
                content = json.loads(ev["raw_content"])
                if content.get("order_id") == order_id or content.get("razorpay_entity_id") == order_id:
                    return {
                        "found": True,
                        "source": "evidence",
                        "evidence_id": ev["evidence_id"],
                        "order_id": content.get("order_id", order_id),
                        "amount": content.get("amount"),
                        "seller_id": content.get("seller_id"),
                        "status": content.get("status"),
                        "order_date": content.get("order_date"),
                    }
            except (json.JSONDecodeError, KeyError):
                continue

        return {"found": False, "reason": f"No order found for {order_id}"}
    finally:
        await db.close()


async def tool_get_payment(tenant_id: str, payment_id: str = "", order_id: str = "") -> dict:
    """Retrieve payment details from Razorpay payments or evidence."""
    from database import get_db

    db = await get_db()
    try:
        # Try Razorpay payments
        if payment_id:
            cursor = await db.execute(
                "SELECT payment_id, order_id, entity_id, amount, currency, status, method, "
                "captured, amount_refunded FROM razorpay_payments "
                "WHERE payment_id = ? AND tenant_id = ?",
                (payment_id, tenant_id),
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "found": True,
                    "source": "razorpay_payments",
                    "payment_id": row["payment_id"],
                    "order_id": row["order_id"],
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "status": row["status"],
                    "method": row["method"],
                    "captured": row["captured"],
                    "amount_refunded": row["amount_refunded"],
                }

        if order_id:
            cursor = await db.execute(
                "SELECT payment_id, order_id, amount, currency, status, method, "
                "captured, amount_refunded FROM razorpay_payments "
                "WHERE order_id = ? AND tenant_id = ?",
                (order_id, tenant_id),
            )
            rows = await cursor.fetchall()
            if rows:
                return {
                    "found": True,
                    "source": "razorpay_payments",
                    "payments": [
                        {
                            "payment_id": r["payment_id"],
                            "amount": r["amount"],
                            "status": r["status"],
                            "method": r["method"],
                            "captured": r["captured"],
                            "amount_refunded": r["amount_refunded"],
                        }
                        for r in rows
                    ],
                    "count": len(rows),
                }

        return {"found": False, "reason": "No payment records found"}
    finally:
        await db.close()


async def tool_get_refund(tenant_id: str, order_id: str) -> dict:
    """Retrieve refund records from evidence."""
    from database import get_db

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT evidence_id, source_type, raw_content FROM evidence "
            "WHERE tenant_id = ? AND source_type = 'refund_record'",
            (tenant_id,),
        )
        rows = await cursor.fetchall()
        refunds = []
        for ev in rows:
            try:
                content = json.loads(ev["raw_content"])
                if content.get("order_id") == order_id:
                    refunds.append({
                        "evidence_id": ev["evidence_id"],
                        "refund_id": content.get("refund_id"),
                        "amount": content.get("amount"),
                        "reason": content.get("reason"),
                        "status": content.get("status"),
                        "return_date": content.get("return_date"),
                    })
            except (json.JSONDecodeError, KeyError):
                continue

        if refunds:
            return {"found": True, "refunds": refunds, "count": len(refunds)}
        return {"found": False, "reason": f"No refund records for order {order_id}"}
    finally:
        await db.close()


async def tool_get_settlement(tenant_id: str, settlement_id: str = "", order_id: str = "") -> dict:
    """Retrieve settlement records from Razorpay settlements."""
    from database import get_db

    db = await get_db()
    try:
        if settlement_id:
            cursor = await db.execute(
                "SELECT settlement_id, amount, currency, status, raw_payload "
                "FROM razorpay_settlements WHERE settlement_id = ? AND tenant_id = ?",
                (settlement_id, tenant_id),
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "found": True,
                    "settlement_id": row["settlement_id"],
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "status": row["status"],
                }

        return {"found": False, "reason": "No settlement records found"}
    finally:
        await db.close()


async def tool_get_delivery(tenant_id: str, order_id: str) -> dict:
    """Retrieve delivery records from evidence."""
    from database import get_db

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT evidence_id, source_type, raw_content FROM evidence "
            "WHERE tenant_id = ? AND source_type = 'delivery'",
            (tenant_id,),
        )
        rows = await cursor.fetchall()
        deliveries = []
        for ev in rows:
            try:
                content = json.loads(ev["raw_content"])
                if content.get("order_id") == order_id:
                    deliveries.append({
                        "evidence_id": ev["evidence_id"],
                        "promised_date": content.get("promised_date"),
                        "actual_date": content.get("actual_date"),
                        "delay_days": content.get("delay_days"),
                        "carrier": content.get("carrier"),
                    })
            except (json.JSONDecodeError, KeyError):
                continue

        if deliveries:
            return {"found": True, "deliveries": deliveries, "count": len(deliveries)}
        return {"found": False, "reason": f"No delivery records for order {order_id}"}
    finally:
        await db.close()


async def tool_get_return(tenant_id: str, order_id: str) -> dict:
    """Retrieve return/reserve records from evidence."""
    from database import get_db

    db = await get_db()
    try:
        # Search both refund_record and complaint sources for return info
        cursor = await db.execute(
            "SELECT evidence_id, source_type, raw_content FROM evidence "
            "WHERE tenant_id = ? AND source_type IN ('refund_record', 'complaint')",
            (tenant_id,),
        )
        rows = await cursor.fetchall()
        returns = []
        for ev in rows:
            try:
                content = json.loads(ev["raw_content"])
                if content.get("order_id") == order_id:
                    returns.append({
                        "evidence_id": ev["evidence_id"],
                        "source_type": ev["source_type"],
                        "refund_id": content.get("refund_id"),
                        "amount": content.get("amount"),
                        "reason": content.get("reason"),
                        "status": content.get("status"),
                        "resolution": content.get("resolution"),
                    })
            except (json.JSONDecodeError, KeyError):
                continue

        if returns:
            return {"found": True, "returns": returns, "count": len(returns)}
        return {"found": False, "reason": f"No return records for order {order_id}"}
    finally:
        await db.close()


async def tool_get_invoice(tenant_id: str, order_id: str) -> dict:
    """Retrieve invoice data from evidence."""
    from database import get_db

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT evidence_id, source_type, raw_content FROM evidence "
            "WHERE tenant_id = ? AND source_type = 'invoice'",
            (tenant_id,),
        )
        rows = await cursor.fetchall()
        invoices = []
        for ev in rows:
            try:
                content = json.loads(ev["raw_content"])
                if content.get("order_id") == order_id:
                    invoices.append({
                        "evidence_id": ev["evidence_id"],
                        "amount": content.get("amount"),
                        "line_items": content.get("line_items"),
                    })
            except (json.JSONDecodeError, KeyError):
                continue

        if invoices:
            return {"found": True, "invoices": invoices, "count": len(invoices)}
        return {"found": False, "reason": f"No invoice records for order {order_id}"}
    finally:
        await db.close()


async def tool_get_policy(tenant_id: str, policy_id: str) -> dict:
    """Retrieve a specific policy clause by policy ID."""
    from database import get_db

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT policy_id, version, clause_text, effective_date FROM policies "
            "WHERE policy_id = ?",
            (policy_id,),
        )
        row = await cursor.fetchone()
        if row:
            return {
                "found": True,
                "policy_id": row["policy_id"],
                "version": row["version"],
                "clause_text": row["clause_text"],
                "effective_date": row["effective_date"],
            }
        return {"found": False, "reason": f"Policy {policy_id} not found"}
    finally:
        await db.close()


async def tool_search_evidence(tenant_id: str, source_type: str) -> dict:
    """Search for evidence records by source type."""
    from database import get_db

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT evidence_id, source_type, raw_content FROM evidence "
            "WHERE tenant_id = ? AND source_type = ?",
            (tenant_id, source_type),
        )
        rows = await cursor.fetchall()
        results = []
        for ev in rows:
            try:
                content = json.loads(ev["raw_content"])
                # Return summary, not full content (bounded data retrieval)
                summary = {
                    "evidence_id": ev["evidence_id"],
                    "source_type": ev["source_type"],
                }
                # Include key identifiers, not full data
                for key in ("order_id", "refund_id", "complaint_id", "settlement_id", "payment_id"):
                    if key in content:
                        summary[key] = content[key]
                if "amount" in content:
                    summary["amount"] = content[key]
                results.append(summary)
            except (json.JSONDecodeError, KeyError):
                results.append({"evidence_id": ev["evidence_id"], "source_type": ev["source_type"]})

        if results:
            return {"found": True, "evidence": results, "count": len(results)}
        return {"found": False, "reason": f"No evidence of type '{source_type}' found"}
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Tool registry — maps tool names to implementations
# ---------------------------------------------------------------------------

TOOL_REGISTRY = {
    "get_order": lambda tid, **kw: tool_get_order(tenant_id=tid, **kw),
    "get_payment": lambda tid, **kw: tool_get_payment(tenant_id=tid, **kw),
    "get_refund": lambda tid, **kw: tool_get_refund(tenant_id=tid, **kw),
    "get_settlement": lambda tid, **kw: tool_get_settlement(tenant_id=tid, **kw),
    "get_delivery": lambda tid, **kw: tool_get_delivery(tenant_id=tid, **kw),
    "get_return": lambda tid, **kw: tool_get_return(tenant_id=tid, **kw),
    "get_invoice": lambda tid, **kw: tool_get_invoice(tenant_id=tid, **kw),
    "get_policy": lambda tid, **kw: tool_get_policy(tenant_id=tid, **kw),
    "search_evidence": lambda tid, **kw: tool_search_evidence(tenant_id=tid, **kw),
}


async def execute_tool(tool_name: str, tenant_id: str, args: dict) -> dict:
    """Execute a tool by name with the given arguments.

    Returns the tool result as a dict. On failure, returns an error dict.
    Never raises — all errors are caught and returned as structured results.
    """
    if tool_name not in TOOL_REGISTRY:
        return {"error": f"Unknown tool: {tool_name}", "found": False}

    try:
        result = await TOOL_REGISTRY[tool_name](tenant_id, **args)
        return result
    except Exception as e:
        logger.error("Tool %s failed: %s", tool_name, str(e))
        return {"error": f"Tool execution failed: {str(e)}", "found": False}
