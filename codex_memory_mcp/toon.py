from __future__ import annotations

import json
import math
import re
from typing import Any


SAFE_STRING = re.compile(r"^[A-Za-z0-9_./@# +\-]+$")
SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
RESERVED = {"true", "false", "null"}


def _flat_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _format_key(key: str) -> str:
    if SAFE_KEY.match(key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _format_value(value: Any, delimiter: str = ",") -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "null"
        return ("%f" % value).rstrip("0").rstrip(".")
    text = str(value)
    needs_quote = (
        text == ""
        or text.strip() != text
        or text.lower() in RESERVED
        or text.startswith("-")
        or "\n" in text
        or "\r" in text
        or "\t" in text
        or delimiter in text
        or ":" in text
        or "[" in text
        or "]" in text
        or "{" in text
        or "}" in text
        or '"' in text
        or not SAFE_STRING.match(text)
    )
    if needs_quote:
        return json.dumps(text, ensure_ascii=False)
    return text


def can_encode_uniform_table(items: Any) -> bool:
    if not isinstance(items, list):
        return False
    if not items:
        return True
    if not all(isinstance(item, dict) for item in items):
        return False
    keys = list(items[0].keys())
    if not keys:
        return False
    for item in items:
        if list(item.keys()) != keys:
            return False
        if not all(_flat_primitive(item[key]) for key in keys):
            return False
    return True


def encode_uniform_table(items: list[dict[str, Any]], name: str = "records") -> str:
    if not can_encode_uniform_table(items):
        raise ValueError("TOON table encoding requires a uniform list of flat objects")
    if not items:
        return f"{_format_key(name)}[0]:"

    keys = list(items[0].keys())
    header = ",".join(_format_key(key) for key in keys)
    lines = [f"{_format_key(name)}[{len(items)}]{{{header}}}:"]
    for item in items:
        row = ",".join(_format_value(item.get(key)) for key in keys)
        lines.append(f"  {row}")
    return "\n".join(lines)


def format_payload(data: Any, requested_format: str = "toon", table_name: str = "records") -> str:
    if requested_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2)
    if requested_format != "toon":
        raise ValueError("format must be 'toon' or 'json'")
    if can_encode_uniform_table(data):
        return encode_uniform_table(data, table_name)
    return json.dumps(data, ensure_ascii=False, indent=2)
