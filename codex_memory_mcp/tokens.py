from __future__ import annotations

from typing import Literal


TokenMethod = Literal["tiktoken", "chars_estimate"]


def count_text_tokens(text: str) -> tuple[int, TokenMethod]:
    try:
        import tiktoken  # type: ignore[import-not-found]

        encoding = tiktoken.get_encoding("o200k_base")
        return len(encoding.encode(text)), "tiktoken"
    except Exception:
        return _rough_count(text), "chars_estimate"


def _rough_count(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, round((ascii_chars / 4.0) + (non_ascii_chars / 2.0)))
