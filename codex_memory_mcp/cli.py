from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import traceback
from typing import Any

from .capture import run_hook_stdin
from .config import codex_home, load_config, write_default_config
from .db import connection, init_db, recent_records, search_records, stats
from .importer import import_codex_home
from .toon import format_payload


HOOK_COMMAND = "py -m codex_memory_mcp hook"
MCP_BLOCK_NAME = "codex-memory"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex-memory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create config and SQLite schema")

    setup = sub.add_parser("setup", help="Run the full Codex Memory setup")
    setup.add_argument("--global", dest="global_scope", action="store_true", help="Use ~/.codex")
    setup.add_argument("--codex-home", type=Path, default=None, help="Codex home to import from")
    setup.add_argument("--skip-import", action="store_true", help="Do not import existing Codex history")
    setup.add_argument("--detailed", action="store_true", help="Also record tool calls and outputs")
    setup.add_argument("--no-stop", action="store_true", help="Do not install the Stop hook")

    hooks = sub.add_parser("install-hooks", help="Install Codex lifecycle hooks")
    hooks.add_argument("--global", dest="global_scope", action="store_true", help="Use ~/.codex")
    hooks.add_argument(
        "--detailed",
        action="store_true",
        help="Also record tool calls, tool outputs, and permission requests",
    )
    hooks.add_argument(
        "--include-stop",
        action="store_true",
        help="Install the Stop hook for assistant final messages (enabled by default)",
    )
    hooks.add_argument(
        "--no-stop",
        action="store_true",
        help="Do not install the Stop hook",
    )

    mcp = sub.add_parser("install-mcp", help="Install Codex MCP server config")
    mcp.add_argument("--global", dest="global_scope", action="store_true", help="Use ~/.codex")

    imp = sub.add_parser("import", help="Import Codex transcripts and history")
    imp.add_argument("--codex-home", type=Path, default=None)

    recent = sub.add_parser("recent", help="Show recent records")
    recent.add_argument("--limit", type=int, default=50)
    recent.add_argument("--session-id", default=None)
    recent.add_argument("--format", choices=["toon", "json"], default="toon")

    search = sub.add_parser("search", help="Search records")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--after", default=None)
    search.add_argument("--before", default=None)
    search.add_argument("--session-id", default=None)
    search.add_argument("--format", choices=["toon", "json"], default="toon")

    sub.add_parser("stats", help="Show aggregate stats")
    sub.add_parser("serve-mcp", help="Run the MCP stdio server")
    sub.add_parser("hook", help="Run one hook event from stdin")

    uninstall = sub.add_parser("uninstall", help="Remove installed hooks and MCP config")
    uninstall.add_argument("--global", dest="global_scope", action="store_true", help="Use ~/.codex")

    args = parser.parse_args(argv)

    if args.command == "init":
        cfg_path = write_default_config()
        cfg = load_config(cfg_path)
        with connection(cfg.db_path) as conn:
            init_db(conn)
        print(f"config={cfg.config_path}")
        print(f"db={cfg.db_path}")
        return 0

    if args.command == "setup":
        result = run_setup(
            global_scope=args.global_scope,
            codex_home_arg=args.codex_home,
            skip_import=args.skip_import,
            detailed=args.detailed,
            include_stop=not args.no_stop,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "install-hooks":
        home = codex_home() if args.global_scope else Path.cwd() / ".codex"
        install_hooks(home, detailed=args.detailed, include_stop=not args.no_stop)
        ensure_hooks_feature(home / "config.toml")
        print(f"hooks={home / 'hooks.json'}")
        return 0

    if args.command == "install-mcp":
        home = codex_home() if args.global_scope else Path.cwd() / ".codex"
        install_mcp(home / "config.toml")
        print(f"config={home / 'config.toml'}")
        return 0

    if args.command == "import":
        result = import_codex_home(args.codex_home)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "recent":
        cfg = load_config()
        with connection(cfg.db_path) as conn:
            data = recent_records(conn, limit=args.limit, session_id=args.session_id)
        print(format_payload(data, args.format))
        return 0

    if args.command == "search":
        cfg = load_config()
        with connection(cfg.db_path) as conn:
            data = search_records(
                conn,
                query=args.query,
                limit=args.limit,
                after=args.after,
                before=args.before,
                session_id=args.session_id,
            )
        print(format_payload(data, args.format))
        return 0

    if args.command == "stats":
        cfg = load_config()
        with connection(cfg.db_path) as conn:
            data = stats(conn)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if args.command == "serve-mcp":
        from .server import main as server_main

        server_main()
        return 0

    if args.command == "hook":
        stdin_text = _read_stdin_utf8()
        event_name = _hook_event_name(stdin_text)
        try:
            run_hook_stdin(stdin_text)
        except Exception:
            _log_hook_exception(event_name)
        if event_name == "Stop":
            print(json.dumps({"continue": True}, separators=(",", ":")))
        return 0

    if args.command == "uninstall":
        home = codex_home() if args.global_scope else Path.cwd() / ".codex"
        uninstall_hooks(home / "hooks.json")
        uninstall_mcp(home / "config.toml")
        print(f"updated={home}")
        return 0

    parser.error("unknown command")
    return 2


def run_setup(
    *,
    global_scope: bool,
    codex_home_arg: Path | None,
    skip_import: bool,
    detailed: bool,
    include_stop: bool,
) -> dict[str, Any]:
    cfg_path = write_default_config()
    cfg = load_config(cfg_path)
    with connection(cfg.db_path) as conn:
        init_db(conn)

    home = codex_home() if global_scope else Path.cwd() / ".codex"
    install_hooks(home, detailed=detailed, include_stop=include_stop)
    install_mcp(home / "config.toml")
    ensure_hooks_feature(home / "config.toml")

    import_result: dict[str, Any] | None = None
    if not skip_import:
        import_result = import_codex_home(codex_home_arg or home)

    return {
        "config": str(cfg.config_path),
        "db": str(cfg.db_path),
        "hooks": str(home / "hooks.json"),
        "codex_config": str(home / "config.toml"),
        "import": import_result,
        "restart_required": True,
    }


def install_hooks(home: Path, *, detailed: bool = False, include_stop: bool = True) -> None:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "hooks.json"
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = path.with_suffix(".json.bak")
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            data = {}

    hooks = data.setdefault("hooks", {})
    selected_events = [
        ("SessionStart", "startup|resume|clear"),
        ("UserPromptSubmit", None),
    ]
    if detailed:
        selected_events.extend(
            [
                ("PreToolUse", "*"),
                ("PostToolUse", "*"),
                ("PermissionRequest", "*"),
            ]
        )
    if include_stop:
        selected_events.append(("Stop", None))

    selected_names = {event for event, _ in selected_events}
    for event in ["PreToolUse", "PostToolUse", "PermissionRequest", "Stop"]:
        if event not in selected_names:
            _remove_memory_hook_event(hooks, event)

    for event, matcher in selected_events:
        groups = hooks.setdefault(event, [])
        _upsert_hook_group(groups, matcher)

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _hook_event_name(stdin_text: str) -> str | None:
    if not stdin_text.strip():
        return None
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        value = payload.get("hook_event_name")
        return str(value) if value is not None else None
    return None


def _read_stdin_utf8() -> str:
    data = sys.stdin.buffer.read()
    return data.decode("utf-8", errors="replace")


def _log_hook_exception(event_name: str | None) -> None:
    try:
        log_path = codex_home() / "memories" / "codex-memory-hook.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} event={event_name or 'unknown'}\n{traceback.format_exc()}\n")
    except Exception:
        pass


def _upsert_hook_group(groups: list[dict[str, Any]], matcher: str | None) -> None:
    for group in groups:
        if group.get("matcher") == matcher or (matcher is None and "matcher" not in group):
            hooks = group.setdefault("hooks", [])
            _upsert_command_hook(hooks)
            return
    group: dict[str, Any] = {"hooks": []}
    if matcher is not None:
        group["matcher"] = matcher
    _upsert_command_hook(group["hooks"])
    groups.append(group)


def _upsert_command_hook(hooks: list[dict[str, Any]]) -> None:
    hooks[:] = [
        hook
        for hook in hooks
        if not (hook.get("type") == "command" and "codex_memory_mcp hook" in hook.get("command", ""))
    ]
    hooks.append(
        {
            "type": "command",
            "command": HOOK_COMMAND,
            "timeout": 30,
        }
    )


def _remove_memory_hook_event(hooks: dict[str, Any], event: str) -> None:
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return
    filtered_groups = []
    for group in groups:
        if not isinstance(group, dict):
            filtered_groups.append(group)
            continue
        group_hooks = group.get("hooks")
        if not isinstance(group_hooks, list):
            filtered_groups.append(group)
            continue
        group["hooks"] = [
            hook
            for hook in group_hooks
            if not (
                isinstance(hook, dict)
                and hook.get("type") == "command"
                and "codex_memory_mcp hook" in hook.get("command", "")
            )
        ]
        if group["hooks"]:
            filtered_groups.append(group)
    if filtered_groups:
        hooks[event] = filtered_groups
    else:
        hooks.pop(event, None)


def ensure_hooks_feature(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if re.search(r"(?m)^\s*codex_hooks\s*=", text) and "[features]" in text:
        text = re.sub(r"(?m)^(\s*codex_hooks\s*=\s*)\w+", r"\1true", text)
    elif re.search(r"(?m)^\[features\]\s*$", text):
        text = re.sub(r"(?m)^(\[features\]\s*)$", r"\1\ncodex_hooks = true", text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n[features]\ncodex_hooks = true\n"
    config_path.write_text(text, encoding="utf-8")


def install_mcp(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    text = _remove_mcp_block(text)
    if text and not text.endswith("\n"):
        text += "\n"
    text += (
        "\n[mcp_servers.codex-memory]\n"
        'command = "py"\n'
        'args = ["-m", "codex_memory_mcp.server"]\n'
        "startup_timeout_sec = 10\n"
        "tool_timeout_sec = 30\n"
    )
    config_path.write_text(text, encoding="utf-8")


def uninstall_hooks(path: Path) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_hooks = group.get("hooks")
            if isinstance(group_hooks, list):
                group["hooks"] = [
                    hook
                    for hook in group_hooks
                    if not (
                        isinstance(hook, dict)
                        and hook.get("type") == "command"
                        and "codex_memory_mcp hook" in hook.get("command", "")
                    )
                ]
        hooks[event] = [group for group in groups if group.get("hooks")]
        if not hooks[event]:
            del hooks[event]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def uninstall_mcp(config_path: Path) -> None:
    if not config_path.exists():
        return
    config_path.write_text(_remove_mcp_block(config_path.read_text(encoding="utf-8")), encoding="utf-8")


def _remove_mcp_block(text: str) -> str:
    lines = text.splitlines()
    result: list[str] = []
    skipping = False
    for line in lines:
        is_header = re.match(r"^\s*\[[^\]]+\]\s*$", line) is not None
        if re.match(r"^\s*\[mcp_servers\.codex-memory\]\s*$", line):
            skipping = True
            continue
        if skipping and is_header:
            skipping = False
        if not skipping:
            result.append(line)
    return "\n".join(result).rstrip() + ("\n" if result else "")


if __name__ == "__main__":
    raise SystemExit(main())
