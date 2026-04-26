from __future__ import annotations

import json
import shlex
from typing import Any

from .config import load_config
from .db import add_record, connection, now_iso, upsert_session
from .privacy import redact_text


EDIT_TOOL_NAMES = {"apply_patch", "edit", "write"}


def is_edit_tool(tool_name: str | None) -> bool:
    if not tool_name:
        return False
    lowered = tool_name.lower()
    return lowered in EDIT_TOOL_NAMES or "apply_patch" in lowered


def _tool_input_command(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, list):
            return " ".join(shlex.quote(str(part)) for part in command)
        if command is not None:
            return str(command)
    return ""


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _extract_response_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _extract_response_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("stdout", "stderr", "aggregated_output", "formatted_output", "output", "text", "message"):
            item = value.get(key)
            if isinstance(item, str) and item:
                parts.append(item)
        content = value.get("content") or value.get("content_items")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(item, str):
                    parts.append(item)
        if parts:
            return "\n".join(parts)
        return _compact_json(value)
    return str(value)


def _record_event(
    conn,
    *,
    event: dict[str, Any],
    record_type: str,
    visible_text: object,
    tool_name: str | None = None,
    role: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    cfg = load_config()
    add_record(
        conn,
        session_id=event.get("session_id"),
        turn_id=event.get("turn_id"),
        ts=event.get("timestamp") or now_iso(),
        record_type=record_type,
        visible_text=visible_text,
        tool_name=tool_name,
        role=role,
        metadata=metadata or {},
        raw_json=event,
        store_raw=cfg.raw_payloads,
        max_visible_chars=cfg.max_visible_chars,
    )


def handle_hook_event(event: dict[str, Any]) -> None:
    cfg = load_config()
    event_name = str(event.get("hook_event_name") or "")
    session_id = event.get("session_id")

    with connection(cfg.db_path) as conn:
        if session_id:
            upsert_session(
                conn,
                str(session_id),
                started_at=event.get("timestamp"),
                updated_at=event.get("timestamp") or now_iso(),
                cwd=event.get("cwd"),
                model=event.get("model"),
                transcript_path=event.get("transcript_path"),
                source="hook",
            )

        if event_name == "SessionStart":
            _record_event(
                conn,
                event=event,
                record_type="session_event",
                visible_text=f"Session started: {event.get('source') or 'unknown'}",
                metadata={"source": event.get("source")},
            )
            return

        if event_name == "UserPromptSubmit":
            _record_event(
                conn,
                event=event,
                record_type="user_prompt",
                visible_text=event.get("prompt", ""),
                role="user",
            )
            return

        if event_name == "PreToolUse":
            tool_name = str(event.get("tool_name") or "")
            tool_input = event.get("tool_input")
            if is_edit_tool(tool_name):
                visible = f"{tool_name} edit attempted"
                metadata = {
                    "input_bytes": len(_compact_json(tool_input).encode("utf-8")),
                    "input_sha256": _sha256(_compact_json(tool_input)),
                }
                record_type = "tool_call"
            elif tool_name.lower() == "bash":
                visible = _tool_input_command(tool_input) or _compact_json(tool_input)
                metadata = {"tool_input": _metadata_without_big_text(tool_input)}
                record_type = "terminal_command"
            else:
                visible = _compact_json(tool_input)
                metadata = {"tool_input": _metadata_without_big_text(tool_input)}
                record_type = "tool_call"
            _record_event(
                conn,
                event=event,
                record_type=record_type,
                visible_text=visible,
                tool_name=tool_name,
                metadata=metadata,
            )
            return

        if event_name == "PostToolUse":
            tool_name = str(event.get("tool_name") or "")
            response = event.get("tool_response")
            if is_edit_tool(tool_name):
                text = _extract_response_text(response)
                visible = f"{tool_name} edit completed"
                metadata = {
                    "output_bytes": len(text.encode("utf-8")),
                    "output_sha256": _sha256(text),
                }
                record_type = "tool_output"
            elif tool_name.lower() == "bash":
                visible = _extract_response_text(response)
                metadata = _response_metadata(response)
                record_type = "terminal_output"
            else:
                visible = _extract_response_text(response)
                metadata = _response_metadata(response)
                record_type = "tool_output"
            _record_event(
                conn,
                event=event,
                record_type=record_type,
                visible_text=visible,
                tool_name=tool_name,
                metadata=metadata,
            )
            return

        if event_name == "PermissionRequest":
            tool_name = str(event.get("tool_name") or "")
            tool_input = event.get("tool_input") or {}
            description = tool_input.get("description") if isinstance(tool_input, dict) else None
            _record_event(
                conn,
                event=event,
                record_type="permission_request",
                visible_text=f"Permission request for {tool_name}: {description or ''}".strip(),
                tool_name=tool_name,
                metadata={"tool_input": _metadata_without_big_text(tool_input)},
            )
            return

        if event_name == "Stop":
            last_message = event.get("last_assistant_message")
            _record_event(
                conn,
                event=event,
                record_type="assistant_message",
                visible_text=last_message or "",
                role="assistant",
                metadata={"stop_hook_active": event.get("stop_hook_active")},
            )
            return

        _record_event(
            conn,
            event=event,
            record_type="session_event",
            visible_text=f"Unhandled hook event: {event_name}",
            metadata={"event": event_name},
        )


def run_hook_stdin(stdin_text: str) -> None:
    if not stdin_text.strip():
        return
    event = json.loads(stdin_text)
    handle_hook_event(event)


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _metadata_without_big_text(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(item, str) and len(item) > 500:
                result[key] = {
                    "text_bytes": len(item.encode("utf-8")),
                    "text_sha256": _sha256(item),
                }
            else:
                result[key] = _metadata_without_big_text(item)
        return result
    if isinstance(value, list):
        return [_metadata_without_big_text(item) for item in value[:20]]
    return value


def _response_metadata(value: Any) -> dict[str, Any]:
    text = _extract_response_text(value)
    metadata: dict[str, Any] = {
        "output_bytes": len(text.encode("utf-8")),
        "output_sha256": _sha256(text),
    }
    if isinstance(value, dict):
        for key in ("exit_code", "status", "duration"):
            if key in value:
                metadata[key] = value[key]
    return metadata


def redacted_json_text(value: Any, max_chars: int) -> str:
    return redact_text(_compact_json(value), max_chars)
