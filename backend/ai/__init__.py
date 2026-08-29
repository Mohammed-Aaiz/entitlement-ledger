"""AI pipeline for evidence extraction and reasoning.

This module uses a configurable LLM provider to:
1. Extract structured facts from evidence documents
2. Reason about claims and policy applicability

Supported providers:
- Ollama (local, free, no API key) — preferred
- Anthropic Claude (requires ANTHROPIC_API_KEY)

The AI NEVER determines financial amounts.
Deterministic calculation engine remains authoritative.
"""
