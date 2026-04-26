from __future__ import annotations

import io
import json
from pathlib import Path
import sys

from codex_memory_mcp.capture import handle_hook_event
from codex_memory_mcp.cli import _read_stdin_utf8, install_hooks, run_setup
from codex_memory_mcp.config import load_config, write_default_config
from codex_memory_mcp.db import add_record, connection, recent_records, search_records, stats
from codex_memory_mcp.importer import import_codex_home
from codex_memory_mcp.server import create_server
from codex_memory_mcp.toon import format_payload


def configure_env(monkeypatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "codex-memory.sqlite"
    cfg_path = tmp_path / "codex-memory.toml"
    monkeypatch.setenv("CODEX_MEMORY_DB", str(db_path))
    monkeypatch.setenv("CODEX_MEMORY_CONFIG", str(cfg_path))
    write_default_config(cfg_path)
    return db_path


def test_hook_events_and_redaction(monkeypatch, tmp_path):
    db_path = configure_env(monkeypatch, tmp_path)
    handle_hook_event(
        {
            "session_id": "s1",
            "turn_id": "t1",
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(tmp_path),
            "model": "gpt-test",
            "prompt": "use token=supersecret123 and sk-1234567890abcdef",
        }
    )
    handle_hook_event(
        {
            "session_id": "s1",
            "turn_id": "t1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
        }
    )
    with connection(db_path) as conn:
        rows = recent_records(conn, limit=10)
    types = {row["record_type"] for row in rows}
    assert "user_prompt" in types
    assert "terminal_command" in types
    combined = "\n".join(row["visible_text"] for row in rows)
    assert "supersecret123" not in combined
    assert "sk-1234567890abcdef" not in combined
    assert "[REDACTED]" in combined


def test_import_idempotency_and_search(monkeypatch, tmp_path):
    db_path = configure_env(monkeypatch, tmp_path)
    home = tmp_path / ".codex"
    session_dir = home / "sessions" / "2026" / "04" / "26"
    session_dir.mkdir(parents=True)
    transcript = session_dir / "rollout-test.jsonl"
    records = [
        {
            "timestamp": "2026-04-26T01:00:00Z",
            "type": "session_meta",
            "payload": {"id": "s1", "timestamp": "2026-04-26T01:00:00Z", "cwd": "C:/repo"},
        },
        {
            "timestamp": "2026-04-26T01:01:00Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "find alpha keyword"},
        },
        {
            "timestamp": "2026-04-26T01:02:00Z",
            "type": "event_msg",
            "payload": {
                "type": "exec_command_end",
                "turn_id": "t1",
                "command": ["pwsh", "-c", "echo alpha"],
                "aggregated_output": "alpha output",
                "exit_code": 0,
                "status": "success",
            },
        },
    ]
    transcript.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    import_codex_home(home)
    import_codex_home(home)
    with connection(db_path) as conn:
        found = search_records(conn, query="alpha", limit=20)
        summary = stats(conn)
    assert len(found) >= 2
    assert summary["records_by_type"]["user_prompt"] == 1
    assert summary["records_by_type"]["terminal_output"] == 1


def test_date_range_recent_and_toon(monkeypatch, tmp_path):
    db_path = configure_env(monkeypatch, tmp_path)
    with connection(db_path) as conn:
        add_record(
            conn,
            session_id="s1",
            turn_id="t1",
            ts="2026-04-26T10:00:00Z",
            record_type="assistant_message",
            visible_text="hello world",
            role="assistant",
        )
        rows = recent_records(conn, limit=1)
    text = format_payload(rows, "toon")
    assert text.startswith("records[1]{")
    assert "assistant_message" in text
    json_text = format_payload(rows, "json")
    assert '"hello world"' in json_text


def test_mcp_server_can_be_created(monkeypatch, tmp_path):
    configure_env(monkeypatch, tmp_path)
    server = create_server()
    assert server is not None


def test_install_hooks_records_turns_quietly_by_default(tmp_path):
    home = tmp_path / ".codex"
    home.mkdir()
    hooks_path = home / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "py -m codex_memory_mcp hook",
                                    "statusMessage": "Recording Codex memory",
                                }
                            ],
                        }
                    ],
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "py -m codex_memory_mcp hook",
                                    "statusMessage": "Recording Codex memory",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    install_hooks(home)

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert set(data["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop"}
    prompt_hook = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert prompt_hook["command"] == "py -m codex_memory_mcp hook"
    assert "statusMessage" not in prompt_hook
    stop_hook = data["hooks"]["Stop"][0]["hooks"][0]
    assert stop_hook["command"] == "py -m codex_memory_mcp hook"
    assert "statusMessage" not in stop_hook


def test_install_hooks_detailed_mode_keeps_tool_hooks(tmp_path):
    home = tmp_path / ".codex"

    install_hooks(home, detailed=True)

    data = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    assert "Stop" in data["hooks"]
    assert "PreToolUse" in data["hooks"]
    assert "PostToolUse" in data["hooks"]
    assert "PermissionRequest" in data["hooks"]


def test_install_hooks_can_disable_stop(tmp_path):
    home = tmp_path / ".codex"

    install_hooks(home, include_stop=False)

    data = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    assert set(data["hooks"]) == {"SessionStart", "UserPromptSubmit"}


def test_hook_stdin_is_decoded_as_utf8(monkeypatch):
    class FakeStdin:
        buffer = io.BytesIO("Türkçe şğı".encode("utf-8"))

    monkeypatch.setattr(sys, "stdin", FakeStdin())

    assert _read_stdin_utf8() == "Türkçe şğı"


def test_setup_installs_hooks_and_mcp(monkeypatch, tmp_path):
    configure_env(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)

    result = run_setup(
        global_scope=False,
        codex_home_arg=None,
        skip_import=True,
        detailed=False,
        include_stop=True,
    )

    hooks = json.loads((tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    config_text = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert set(hooks["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop"}
    assert "[mcp_servers.codex-memory]" in config_text
    assert result["restart_required"] is True
