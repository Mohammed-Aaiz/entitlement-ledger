"""Evidence extraction module.

Sends evidence documents to the LLM provider and requests structured JSON facts.
Every extracted fact must contain a source quote.
"""
import json
import os
import logging
from typing import Optional

from ai.llm_provider import get_provider

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM = (
    "You are a financial evidence extraction system. "
    "Extract structured facts from evidence documents. "
    "Every fact MUST include a direct quote from the source document. "
    "Do NOT invent information not present in the document. "
    "Do NOT calculate or determine any monetary amounts. "
    "Return ONLY valid JSON, no explanation text."
)

EXTRACTION_PROMPT = """Extract structured facts from the following evidence document.

Evidence Type: {source_type}
Document ID: {evidence_id}

Document Content:
{content}

Return a JSON object with this exact structure:
{{
  "facts": [
    {{
      "fact_type": "order_detail|delivery_record|complaint|refund|policy_reference|other",
      "value": "string description of the fact",
      "amount": null,
      "date": null,
      "evidence_quote": "exact quote from the document supporting this fact"
    }}
  ]
}}

Rules:
- If the document contains monetary amounts, include them in the "amount" field as a number (not formatted).
- If the document contains dates, include them in the "date" field as ISO format.
- Every fact MUST have an evidence_quote that is a direct quote from the document.
- Return ONLY the JSON object, no markdown fences or explanation."""


def validate_extraction_response(response: dict) -> bool:
    """Validate the structure of an extraction response."""
    if not isinstance(response, dict):
        return False
    if "facts" not in response:
        return False
    if not isinstance(response["facts"], list):
        return False
    for fact in response["facts"]:
        if not isinstance(fact, dict):
            return False
        required_fields = ["fact_type", "value", "evidence_quote"]
        for field in required_fields:
            if field not in fact:
                return False
        if not fact["evidence_quote"] or len(fact["evidence_quote"].strip()) < 5:
            return False
    return True


def extract_facts_from_evidence(
    evidence_id: str,
    source_type: str,
    raw_content: str,
    max_retries: int = 1,
) -> dict:
    """Extract structured facts from an evidence document using the LLM provider.

    Args:
        evidence_id: The evidence document ID
        source_type: Type of evidence (order, delivery, complaint, etc.)
        raw_content: The raw document content
        max_retries: Number of retries for malformed responses

    Returns:
        dict with "facts" list, each containing fact_type, value, amount, date, evidence_quote
    """
    provider = get_provider()

    prompt = EXTRACTION_PROMPT.format(
        source_type=source_type,
        evidence_id=evidence_id,
        content=raw_content,
    )

    for attempt in range(max_retries + 1):
        try:
            parsed = provider.complete_json(
                prompt,
                system=EXTRACTION_SYSTEM,
                max_tokens=2048,
                temperature=0.0,
            )

            if validate_extraction_response(parsed):
                logger.info(
                    "Extraction successful for %s: %d facts extracted",
                    evidence_id,
                    len(parsed["facts"]),
                )
                return parsed
            else:
                logger.warning(
                    "Extraction response validation failed for %s (attempt %d/%d)",
                    evidence_id,
                    attempt + 1,
                    max_retries + 1,
                )
                if attempt < max_retries:
                    continue
                raise ValueError(
                    f"Extraction response validation failed after {max_retries + 1} attempts"
                )

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "Extraction error for %s (attempt %d/%d): %s",
                evidence_id,
                attempt + 1,
                max_retries + 1,
                str(e),
            )
            if attempt < max_retries:
                continue
            raise ValueError(f"Extraction failed after {max_retries + 1} attempts: {e}")

    raise ValueError(f"Extraction failed after {max_retries + 1} attempts")
