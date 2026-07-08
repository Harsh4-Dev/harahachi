"""Backwards-compatible shim.

The single-URL vLLM client was refactored into the backend-registry-aware client
in ``backends.py`` (v2). This module now just re-exports the same surface so any
legacy import keeps working. Prefer importing from ``. import backends`` directly.
"""
from __future__ import annotations

from .backends import (  # noqa: F401
    ANALYZE_SYSTEM_PROMPT,
    BackendUnavailable,
    VLLMUnavailable,
    analyze_image,
    check_health,
    is_configured,
    passthrough_chat_completions,
    passthrough_models,
    phrase_suggestions,
)


async def chat(messages: list[dict]) -> str:
    """Legacy nutrition-coach chat helper (used by nothing in v2, kept for compat)."""
    from .backends import chat_completion
    from .chat_assistant import ANNAI_PERSONA
    convo = [{"role": "system", "content": ANNAI_PERSONA}]
    convo.extend(messages)
    return await chat_completion(convo, max_tokens=512, temperature=0.6)
