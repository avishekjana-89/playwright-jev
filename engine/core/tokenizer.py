"""
engine/core/tokenizer.py

Provides utility functions for token-based string truncation.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

try:
    import tiktoken
    _tokenizer = tiktoken.get_encoding("cl100k_base")
    HAS_TOKENIZER = True
except Exception as e:
    log.warning("tiktoken not installed or failed to load. Falling back to char-based truncation.", exc_info=e)
    HAS_TOKENIZER = False
    _tokenizer = None


def truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, int, int]:
    """
    Truncates text to a maximum number of tokens.
    Returns: (truncated_text, original_tokens, final_tokens)
    """
    if not text:
        return "", 0, 0

    if not HAS_TOKENIZER or _tokenizer is None:
        # Rough heuristic: 1 token ≈ 4 chars
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text, len(text) // 4, len(text) // 4
        return text[:max_chars], len(text) // 4, max_tokens

    tokens = _tokenizer.encode(text)
    orig_len = len(tokens)
    
    if orig_len <= max_tokens:
        return text, orig_len, orig_len
        
    truncated = _tokenizer.decode(tokens[:max_tokens])
    return truncated, orig_len, max_tokens
