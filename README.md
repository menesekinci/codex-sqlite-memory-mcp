# Codex SQLite Memory MCP

SQLite-backed long-term memory for Codex sessions. It records Codex hook events,
imports existing Codex JSONL transcripts, redacts visible secrets by default, and
exposes history through MCP tools.

## Quick Start

```powershell
py -m pip install -e .[dev]
codex-memory setup --global
```

Restart Codex after installing hooks or MCP config.

`codex-memory setup --global` is the one-command installer after the package is
available in the Python environment. It creates the SQLite DB/config, imports
existing Codex history, installs quiet hooks, and registers the MCP server in
Codex config.

`install-hooks` is quiet by default: it records session starts, user prompts,
and assistant final messages without a visible hook status message. It
intentionally skips per-tool hooks so Codex Desktop does not show
`Recording Codex memory` on every agent step.

Use `codex-memory install-hooks --global --detailed` only if you want live
per-tool capture. Use `--no-stop` only if your Codex build does not handle
`Stop` hook responses cleanly. Hook failures are fail-open and logged to
`%USERPROFILE%\.codex\memories\codex-memory-hook.log`.

## MCP Tools

- `memory_recent`
- `memory_search`
- `memory_by_date`
- `memory_sessions`
- `memory_get_session`
- `memory_session_info`
- `memory_delete_session`
- `memory_stats`

List responses default to `format="toon"` and accept `format="json"`.

`memory_session_info` reports record counts, estimated logical storage size, and
token usage. Token usage prefers imported Codex `token_count` events when
available; stored visible text tokens are counted with `tiktoken` when installed
or a clearly marked character-based estimate otherwise.

`memory_delete_session` requires `confirm=true` and removes the session plus its
records from SQLite. The equivalent CLI commands are:

```powershell
codex-memory session-info <session-id>
codex-memory delete-session <session-id> --yes
```

## Storage

Default database:

```text
%USERPROFILE%\.codex\memories\codex-memory.sqlite
```

Default config:

```text
%USERPROFILE%\.codex\memories\codex-memory.toml
```

Visible text is redacted by default. Raw hook payload storage is disabled unless
`raw_payloads = true` is set in the config.
