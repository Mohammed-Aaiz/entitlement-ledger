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
    
    The input MUST be pre-ordered as the actual chain (first decision's
    prev_decision_hash == "genesis", each subsequent decision's
    prev_decision_hash == previous decision's decision_hash).

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


def verify_chain_by_links(decisions: list[dict]) -> dict:
    """Verify hash chains by following prev_decision_hash cryptographic links.

    This does NOT depend on created_at ordering (multiple decisions can
    legitimately share identical timestamps, and timestamp formats can
    differ across writers).  Instead it:

      1. identifies the chain head(s) — decisions whose own hash is not
         referenced as any other decision's prev_decision_hash,
      2. walks each head backwards through prev_decision_hash links to
         genesis, recomputing every hash,
      3. reports diagnostics for:
         - invalid_hash        — stored hash != recomputed hash (tampering)
         - missing_predecessor — prev_decision_hash not present in the set
         - duplicate_hash      — two decisions claim the same decision_hash
         - cycle               — a loop in the link structure
         - disconnected        — decision not reachable from any chain head

    Returns:
        {
            valid: bool,          # True iff every decision verifies and the
                                  # tenant forms a single connected chain
            checked_count: int,   # decisions whose hash recomputed correctly
            break_at: str | None, # decision_id of the first issue found
            chains: int,          # number of independent chains (heads)
            heads: list[str],     # head decision_ids (latest in each chain)
            issues: list[dict],   # [{decision_id, issue, detail}, ...]
        }
    """
    if not decisions:
        # Nothing to verify — vacuous validity, matching verify_chain([]).
        return {
            "valid": True,
            "checked_count": 0,
            "break_at": None,
            "chains": 0,
            "heads": [],
            "issues": [],
        }

    by_hash: dict[str, list[dict]] = {}
    for decision in decisions:
        dhash = decision.get("decision_hash")
        if dhash:
            by_hash.setdefault(dhash, []).append(decision)

    # Chain heads: decisions whose own hash is never referenced as another
    # decision's prev_decision_hash.  These are the LATEST decisions.
    referenced: set[str] = set()
    for decision in decisions:
        prev = decision.get("prev_decision_hash") or "genesis"
        if prev != "genesis":
            referenced.add(prev)
    heads = [d for d in decisions if d.get("decision_hash") not in referenced]
    # Stable, deterministic diagnostic order (newest head first).
    heads.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)

    issues: list[dict] = []
    break_at = None
    checked = 0
    global_visited: set[str] = set()

    def _issue(decision_id: str, issue: str, detail: str) -> None:
        nonlocal break_at
        issues.append({
            "decision_id": decision_id,
            "issue": issue,
            "detail": detail,
        })
        if break_at is None:
            break_at = decision_id

    for head in heads:
        current = head
        path: list[dict] = []
        path_ids: set[str] = set()
        while current is not None:
            did = current["decision_id"]
            if did in global_visited:
                # Merged into an already-verified prefix (fork) — that
                # prefix was checked on an earlier head's walk.
                break
            if did in path_ids:
                _issue(did, "cycle", "decision revisited while walking the chain")
                break
            path.append(current)
            path_ids.add(did)

            prev = current.get("prev_decision_hash") or "genesis"
            expected = compute_decision_hash(current, prev)
            if current.get("decision_hash") != expected:
                _issue(
                    did, "invalid_hash",
                    "stored decision_hash does not match recomputed hash (tampering?)",
                )
                break
            checked += 1

            if prev == "genesis":
                break
            predecessors = by_hash.get(prev)
            if not predecessors:
                _issue(
                    did, "missing_predecessor",
                    f"prev_decision_hash {prev[:16]}... not found in tenant decisions",
                )
                break
            if len(predecessors) > 1:
                _issue(
                    did, "duplicate_hash",
                    f"multiple decisions claim decision_hash {prev[:16]}...",
                )
                break
            current = predecessors[0]

        global_visited.update(path_ids)

    # Decisions never reached from any head form disconnected fragments.
    unreachable = [d for d in decisions if d["decision_id"] not in global_visited]
    for decision in unreachable:
        _issue(
            decision["decision_id"], "disconnected",
            "decision is not reachable from any chain head",
        )

    # A healthy tenant ledger is exactly ONE connected chain.  Multiple
    # heads mean the ledger has forked or been tampered with.
    valid = not issues and len(heads) == 1
    if len(heads) != 1:
        for head in heads[1:]:
            _issue(
                head["decision_id"], "disconnected",
                "additional chain head found — ledger is not a single chain",
            )

    return {
        "valid": valid,
        "checked_count": checked,
        "break_at": break_at,
        "chains": len(heads),
        "heads": [h["decision_id"] for h in heads],
        "issues": issues,
    }
