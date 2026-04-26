from __future__ import annotations

import re


SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{10,}"), "sk-[REDACTED]"),
    (re.compile(r"ghp_[A-Za-z0-9_]{10,}"), "ghp_[REDACTED]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{10,}"), "github_pat_[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]{8,}"), "Bearer [REDACTED]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|authorization)"
            r"(\s*[:=]\s*)(['\"]?)([^'\"\s,;]+)"
        ),
        r"\1\2\3[REDACTED]",
    ),
    (
        re.compile(
            r"(?im)^([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD)[A-Z0-9_]*"
            r"\s*=\s*)(.+)$"
        ),
        r"\1[REDACTED]",
    ),
]


def redact_text(value: object, max_chars: int | None = None) -> str:
    text = "" if value is None else str(value)
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    if max_chars is not None and len(text) > max_chars:
        omitted = len(text) - max_chars
        text = f"{text[:max_chars]}\n[... truncated {omitted} chars ...]"
    return text
