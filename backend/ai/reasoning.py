"""Reasoning module.

Takes extracted facts + policy information and reasons about claims.
The AI determines WHAT happened and WHICH policies apply.
The AI NEVER determines financial amounts.
"""
import json
import os
import logging
from typing import Optional

from ai.llm_provider import get_provider

logger = logging.getLogger(__name__)

REASONING_SYSTEM = (
    "You are a financial decision reasoning system for a marketplace platform. "
    "You have received extracted facts from evidence documents and applicable policy clauses. "
    "Determine WHAT claims are supported by the evidence and WHICH policies apply. "
    "CRITICAL RULES:\n"
    "- You MUST NOT determine or calculate any monetary amounts.\n"
    "- You MUST reference specific evidence_ids for each claim.\n"
    "- You MUST reference specific policy_clause_id for each claim.\n"
    "- You MUST explain your reasoning clearly.\n"
    "- If evidence is insufficient, say so — do NOT invent deductions.\n"
    "- Classification must be one of: \"clear\", \"exception\", \"ambiguous\".\n"
    "Return ONLY valid JSON, no explanation text."
)

REASONING_PROMPT = """Determine what claims are supported by the evidence and which policies apply.

Evidence Facts:
{extracted_facts}

Applicable Policies:
{policies}

Return a JSON object with this exact structure:
{{
  "claims": [
    {{
      "claim_type": "sla_breach|return_processed|no_penalty|platform_fee|other",
      "policy_clause_id": "the_clause_id this claim relates to",
      "evidence_ids": ["list of evidence_ids supporting this claim"],
      "reasoning": "clear explanation of why this claim is supported"
    }}
  ],
  "classification": "clear|exception|ambiguous",
  "confidence": 0.0,
  "reasoning_summary": "brief summary of overall reasoning"
}}

IMPORTANT:
- classification "clear" = evidence clearly supports the claims
- classification "exception" = special circumstances requiring review
- classification "ambiguous" = insufficient or contradictory evidence
- confidence must be between 0.0 and 1.0
- Return ONLY the JSON object, no markdown fences or explanation."""


def validate_reasoning_response(response: dict) -> bool:
    """Validate the structure of a reasoning response."""
    if not isinstance(response, dict):
        return False

    required_fields = ["claims", "classification", "confidence", "reasoning_summary"]
    for field in required_fields:
        if field not in response:
            return False

    if response["classification"] not in ("clear", "exception", "ambiguous"):
        return False

    if not isinstance(response["confidence"], (int, float)):
        return False
    if response["confidence"] < 0.0 or response["confidence"] > 1.0:
        return False

    if not isinstance(response["claims"], list):
        return False

    for claim in response["claims"]:
        if not isinstance(claim, dict):
            return False
        required_claim_fields = ["claim_type", "policy_clause_id", "evidence_ids", "reasoning"]
        for field in required_claim_fields:
            if field not in claim:
                return False
        if not isinstance(claim["evidence_ids"], list):
            return False
        if len(claim["evidence_ids"]) == 0:
            return False
        # Reject claims that contain monetary amounts in reasoning
        reasoning = claim.get("reasoning", "").lower()
        if "rupees" in reasoning or "inr" in reasoning or "₹" in reasoning:
            if "deduct" in reasoning or "penalty amount" in reasoning:
                if any(char.isdigit() for char in reasoning.split("deduct")[-1][:20]):
                    logger.warning("Claim reasoning contains monetary assertion")
                    return False

    return True


def reason_about_claims(
    extracted_facts: dict,
    policies: list[dict],
    max_retries: int = 1,
) -> dict:
    """Reason about claims using extracted facts and policies.

    Args:
        extracted_facts: Dict with "facts" list from extraction step
        policies: List of policy dicts with policy_id, clause_text, version
        max_retries: Number of retries for malformed responses

    Returns:
        dict with claims, classification, confidence, reasoning_summary
    """
    provider = get_provider()

    # Format policies for prompt
    policies_text = "\n".join([
        f"Policy: {p['policy_id']} (v{p['version']})\n{p['clause_text']}"
        for p in policies
    ])

    # Format facts for prompt
    facts_text = json.dumps(extracted_facts, indent=2)

    prompt = REASONING_PROMPT.format(
        extracted_facts=facts_text,
        policies=policies_text,
    )

    for attempt in range(max_retries + 1):
        try:
            parsed = provider.complete_json(
                prompt,
                system=REASONING_SYSTEM,
                max_tokens=2048,
                temperature=0.0,
            )

            if validate_reasoning_response(parsed):
                logger.info(
                    "Reasoning successful: %d claims, classification=%s",
                    len(parsed["claims"]),
                    parsed["classification"],
                )
                return parsed
            else:
                logger.warning(
                    "Reasoning response validation failed (attempt %d/%d)",
                    attempt + 1,
                    max_retries + 1,
                )
                if attempt < max_retries:
                    continue
                raise ValueError(
                    f"Reasoning response validation failed after {max_retries + 1} attempts"
                )

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "Reasoning error (attempt %d/%d): %s",
                attempt + 1,
                max_retries + 1,
                str(e),
            )
            if attempt < max_retries:
                continue
            raise ValueError(f"Reasoning failed after {max_retries + 1} attempts: {e}")

    raise ValueError(f"Reasoning failed after {max_retries + 1} attempts")
