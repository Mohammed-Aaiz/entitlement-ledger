"""Tamper-evident decision hash chain.

Uses SHA-256 with deterministic sorted-key JSON canonicalization.
DO NOT call this blockchain.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Optional


# Match ISO-ish timestamps: "2025-01-01T00:00:00Z", "2025-01-01 00:00:00+00:00",
# "2025-01-01T00:00:00.123456", "2025-01-01 00:00:00", etc.
_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)


def _normalize_timestamp(val: str) -> str:
    """Normalize any ISO timestamp to canonical form: 'YYYY-MM-DDTHH:MM:SS+00:00'."""
    if not _TS_RE.match(val):
        return val
    # Replace space with T
    val = val.replace(" ", "T", 1)
    # Strip fractional seconds
    val = re.sub(r"\.\d+", "", val)
    # Normalize Z to +00:00
    if val.endswith("Z"):
        val = val[:-1] + "+00:00"
    # If no timezone offset, append +00:00 (assume UTC)
    if not re.search(r"[+-]\d{2}:\d{2}$", val):
        val += "+00:00"
    return val


def _normalize_value(v):
    """Recursively normalize datetime objects and timestamp strings."""
    # Handle datetime objects directly (from PostgreSQL TIMESTAMPTZ columns)
    if isinstance(v, datetime):
        # Normalize to UTC then to canonical string
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    if isinstance(v, str) and len(v) >= 19 and _TS_RE.match(v):
        return _normalize_timestamp(v)
    if isinstance(v, list):
        return [_normalize_value(item) for item in v]
    if isinstance(v, dict):
        return {k: _normalize_value(val) for k, val in v.items()}
    return v


def canonicalize(data: dict) -> str:
    """Produce deterministic sorted-key JSON, excluding decision_hash.

    Timestamps (both strings and datetime objects) are normalized to a
    canonical form so the hash chain survives database round-trips
    across SQLite and PostgreSQL.
    """
    cleaned = {k: v for k, v in data.items() if k != "decision_hash"}
    cleaned = _normalize_value(cleaned)
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":"), default=str)


def compute_decision_hash(decision_data: dict, prev_hash: str) -> str:
    """Compute SHA-256 hash for a decision record."""
    canonical = canonicalize(decision_data)
    payload = canonical + prev_hash
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_chain(decisions: list[dict]) -> dict:
    """Verify the integrity of a chain of decisions.
    
    Returns:
        {
            valid: bool,
            checked_count: int,
            break_at: str | None  # decision_id where chain breaks
        }
    """
    prev_hash = "genesis"
    checked = 0

    for decision in decisions:
        expected_hash = compute_decision_hash(decision, prev_hash)
        if decision["decision_hash"] != expected_hash:
            return {
                "valid": False,
                "checked_count": checked,
                "break_at": decision["decision_id"],
            }
        prev_hash = decision["decision_hash"]
        checked += 1

    return {
        "valid": True,
        "checked_count": checked,
        "break_at": None,
    }
