from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import shlex
from typing import Any

from .capture import _compact_json, _extract_response_text, is_edit_tool
from .config import codex_home, load_config
from .db import add_record, connection, now_iso, upsert_session


def import_codex_home(codex_home_path: Path | None = None) -> dict[str, int]:
    cfg = load_config()
    home = codex_home_path or codex_home()
    stats = {"files": 0, "lines": 0, "inserted": 0, "skipped": 0}
    with connection(cfg.db_path) as conn:
        session_index = home / "session_index.jsonl"
        if session_index.exists():
            _import_session_index(conn, session_index)

        history = home / "history.jsonl"
        if history.exists():
            file_stats = _import_history(conn, history, cfg.raw_payloads, cfg.max_visible_chars)
            _merge_stats(stats, file_stats)

        sessions_dir = home / "sessions"
        if sessions_dir.exists():
            for path in sorted(sessions_dir.rglob("*.jsonl")):
                file_stats = _import_transcript(conn, path, cfg.raw_payloads, cfg.max_visible_chars)
                _merge_stats(stats, file_stats)
    return stats


def _merge_stats(total: dict[str, int], part: dict[str, int]) -> None:
    for key, value in part.items():
        total[key] = total.get(key, 0) + value


def _safe_json_lines(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                yield line_number, json.loads(line)
            except json.JSONDecodeError:
                continue


def _import_session_index(conn, path: Path) -> None:
    for _, item in _safe_json_lines(path):
        session_id = item.get("id")
        if not session_id:
            continue
        upsert_session(
            conn,
            str(session_id),
            updated_at=_ts_to_iso(item.get("updated_at")),
            source="session_index",
        )


def _import_history(conn, path: Path, store_raw: bool, max_visible_chars: int) -> dict[str, int]:
    stats = {"files": 1, "lines": 0, "inserted": 0, "skipped": 0}
    for line_number, item in _safe_json_lines(path):
        stats["lines"] += 1
        session_id = item.get("session_id")
        ts = _ts_to_iso(item.get("ts")) or now_iso()
        text = item.get("text", "")
        result = add_record(
            conn,
            session_id=str(session_id) if session_id else None,
            turn_id=None,
            ts=ts,
            record_type="user_prompt",
            visible_text=text,
            role="user",
            metadata={"source": "history"},
            source_path=str(path),
            source_line=line_number,
            raw_json=item,
            store_raw=store_raw,
            max_visible_chars=max_visible_chars,
        )
        stats["inserted" if result.inserted else "skipped"] += 1
    return stats


def _import_transcript(conn, path: Path, store_raw: bool, max_visible_chars: int) -> dict[str, int]:
    stats = {"files": 1, "lines": 0, "inserted": 0, "skipped": 0}
    session_id: str | None = None
    call_tools: dict[str, tuple[str | None, str | None]] = {}

    for line_number, item in _safe_json_lines(path):
        stats["lines"] += 1
        ts = _ts_to_iso(item.get("timestamp")) or now_iso()
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        top_type = item.get("type")
        payload_type = payload.get("type") if isinstance(payload, dict) else None

        if top_type == "session_meta":
            session_id = str(payload.get("id") or session_id or _session_id_from_path(path))
            upsert_session(
                conn,
                session_id,
                started_at=_ts_to_iso(payload.get("timestamp")) or ts,
                updated_at=ts,
                cwd=payload.get("cwd"),
                model=_model_from_payload(payload),
                transcript_path=str(path),
                source="transcript",
            )
            inserted = _insert(
                conn,
                path,
                line_number,
                1,
                session_id=session_id,
                turn_id=None,
                ts=ts,
                record_type="session_event",
                visible_text=f"Imported session: {session_id}",
                metadata={"originator": payload.get("originator"), "source": payload.get("source")},
                raw_json=item,
                store_raw=store_raw,
                max_visible_chars=max_visible_chars,
            )
            stats["inserted" if inserted else "skipped"] += 1
            continue

        if not session_id:
            session_id = _session_id_from_path(path)
            upsert_session(conn, session_id, updated_at=ts, transcript_path=str(path), source="transcript")

        inserted_count = 0
        skipped_count = 0
        for sub_index, record in enumerate(
            _records_from_payload(payload, top_type, payload_type, call_tools),
            start=1,
        ):
            inserted = _insert(
                conn,
                path,
                line_number,
                sub_index,
                session_id=session_id,
                ts=ts,
                raw_json=item,
                store_raw=store_raw,
                max_visible_chars=max_visible_chars,
                **record,
            )
            if inserted:
                inserted_count += 1
            else:
                skipped_count += 1
        stats["inserted"] += inserted_count
        stats["skipped"] += skipped_count
    return stats


def _records_from_payload(
    payload: dict[str, Any],
    top_type: str | None,
    payload_type: str | None,
    call_tools: dict[str, tuple[str | None, str | None]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    turn_id = payload.get("turn_id") or payload.get("turnId")

    if top_type == "turn_context":
        return [
            {
                "turn_id": payload.get("turn_id"),
                "record_type": "session_event",
                "visible_text": f"Turn context: {payload.get('cwd') or ''}".strip(),
                "metadata": {
                    "model": payload.get("model"),
                    "cwd": payload.get("cwd"),
                    "approval_policy": payload.get("approval_policy"),
                },
            }
        ]

    if payload_type == "message":
        role = payload.get("role")
        text = _message_content_text(payload.get("content"))
        if role == "user":
            record_type = "user_prompt"
        elif role == "assistant":
            record_type = "assistant_message"
        else:
            record_type = "session_event"
        return [
            {
                "turn_id": turn_id,
                "record_type": record_type,
                "visible_text": text,
                "role": role,
                "metadata": {"payload_type": payload_type},
            }
        ]

    if payload_type == "user_message":
        return [
            {
                "turn_id": turn_id,
                "record_type": "user_prompt",
                "visible_text": payload.get("message", ""),
                "role": "user",
                "metadata": {"payload_type": payload_type},
            }
        ]

    if payload_type == "agent_message":
        return [
            {
                "turn_id": turn_id,
                "record_type": "assistant_message",
                "visible_text": payload.get("message", ""),
                "role": "assistant",
                "metadata": {"phase": payload.get("phase")},
            }
        ]

    if payload_type == "task_complete":
        return [
            {
                "turn_id": turn_id,
                "record_type": "assistant_message",
                "visible_text": payload.get("last_agent_message", ""),
                "role": "assistant",
                "metadata": {
                    "duration_ms": payload.get("duration_ms"),
                    "time_to_first_token_ms": payload.get("time_to_first_token_ms"),
                },
            }
        ]

    if payload_type == "exec_command_end":
        command = _command_text(payload.get("command"))
        output = payload.get("aggregated_output") or payload.get("formatted_output") or ""
        if command:
            records.append(
                {
                    "turn_id": turn_id,
                    "record_type": "terminal_command",
                    "visible_text": command,
                    "tool_name": "Bash",
                    "metadata": {
                        "cwd": payload.get("cwd"),
                        "exit_code": payload.get("exit_code"),
                        "status": payload.get("status"),
                    },
                }
            )
        if output:
            records.append(
                {
                    "turn_id": turn_id,
                    "record_type": "terminal_output",
                    "visible_text": output,
                    "tool_name": "Bash",
                    "metadata": {
                        "exit_code": payload.get("exit_code"),
                        "status": payload.get("status"),
                        "stdout_bytes": len(str(payload.get("stdout") or "").encode("utf-8")),
                        "stderr_bytes": len(str(payload.get("stderr") or "").encode("utf-8")),
                    },
                }
            )
        return records

    if payload_type in {"function_call", "tool_search_call"}:
        call_id = payload.get("call_id")
        tool_name = payload.get("name") or payload.get("type")
        namespace = payload.get("namespace")
        if call_id:
            call_tools[str(call_id)] = (str(tool_name) if tool_name else None, str(namespace) if namespace else None)
        arguments = payload.get("arguments")
        visible = _arguments_text(arguments)
        if _is_terminal_tool(tool_name, namespace):
            record_type = "terminal_command"
        elif is_edit_tool(str(tool_name or "")):
            visible = f"{tool_name} edit attempted"
            record_type = "tool_call"
        else:
            record_type = "tool_call"
        return [
            {
                "turn_id": turn_id,
                "record_type": record_type,
                "visible_text": visible,
                "tool_name": str(tool_name) if tool_name else None,
                "metadata": {"namespace": namespace, "call_id": call_id},
            }
        ]

    if payload_type in {"function_call_output", "tool_search_output"}:
        call_id = payload.get("call_id")
        tool_name, namespace = call_tools.get(str(call_id), (None, None))
        output = payload.get("output")
        record_type = "terminal_output" if _is_terminal_tool(tool_name, namespace) else "tool_output"
        if is_edit_tool(tool_name):
            output = f"{tool_name} edit completed"
        return [
            {
                "turn_id": turn_id,
                "record_type": record_type,
                "visible_text": output,
                "tool_name": tool_name,
                "metadata": {"namespace": namespace, "call_id": call_id},
            }
        ]

    if payload_type == "mcp_tool_call_end":
        invocation = payload.get("invocation") if isinstance(payload.get("invocation"), dict) else {}
        tool_name = invocation.get("tool")
        result = payload.get("result")
        return [
            {
                "turn_id": turn_id,
                "record_type": "tool_output",
                "visible_text": _extract_response_text(result),
                "tool_name": tool_name,
                "metadata": {
                    "server": invocation.get("server"),
                    "duration": payload.get("duration"),
                    "call_id": payload.get("call_id"),
                },
            }
        ]

    if payload_type == "dynamic_tool_call_request":
        return [
            {
                "turn_id": turn_id,
                "record_type": "tool_call",
                "visible_text": _compact_json(payload.get("arguments")),
                "tool_name": payload.get("tool"),
                "metadata": {"namespace": payload.get("namespace"), "call_id": payload.get("callId")},
            }
        ]

    if payload_type == "dynamic_tool_call_response":
        return [
            {
                "turn_id": turn_id,
                "record_type": "tool_output",
                "visible_text": _extract_response_text(payload.get("content_items") or payload.get("error")),
                "tool_name": payload.get("tool"),
                "metadata": {
                    "namespace": payload.get("namespace"),
                    "call_id": payload.get("call_id"),
                    "success": payload.get("success"),
                    "duration": payload.get("duration"),
                },
            }
        ]

    return []


def _insert(conn, path: Path, line_number: int, sub_index: int, **kwargs) -> bool:
    source_line = line_number * 100 + sub_index
    result = add_record(
        conn,
        source_path=str(path),
        source_line=source_line,
        **kwargs,
    )
    return result.inserted


def _message_content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(parts)


def _command_text(command: Any) -> str:
    if isinstance(command, list):
        return " ".join(shlex.quote(str(part)) for part in command)
    return "" if command is None else str(command)


def _arguments_text(arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
            if isinstance(decoded, dict) and "command" in decoded:
                return _command_text(decoded.get("command"))
            return _compact_json(decoded)
        except json.JSONDecodeError:
            return arguments
    if isinstance(arguments, dict) and "command" in arguments:
        return _command_text(arguments.get("command"))
    return _compact_json(arguments)


def _is_terminal_tool(tool_name: Any, namespace: Any) -> bool:
    text = f"{namespace or ''}.{tool_name or ''}".lower()
    return "shell_command" in text or text.endswith(".bash") or tool_name == "Bash"


def _session_id_from_path(path: Path) -> str:
    return path.stem


def _model_from_payload(payload: dict[str, Any]) -> str | None:
    provider = payload.get("model_provider")
    if isinstance(provider, dict):
        return provider.get("model") or provider.get("name")
    return None


def _ts_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number = number / 1000.0
        return datetime.fromtimestamp(number, tz=datetime.now().astimezone().tzinfo).astimezone().isoformat()
    return str(value)


def date_range(date: str | None, after: str | None, before: str | None) -> tuple[str, str]:
    if after and before:
        return after, before
    if date:
        start = datetime.fromisoformat(date)
    else:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return after or start.isoformat(), before or end.isoformat()
