# Codex SQLite Memory MCP

SQLite-backed long-term memory for Codex sessions. It records Codex hook events,
imports existing Codex JSONL transcripts, redacts visible secrets by default, and
exposes history through MCP tools.

## Quick Start

```powershell
py -m pip install -e .[dev]
codex-memory init
codex-memory import
codex-memory install-hooks --global
codex-memory install-mcp --global
```

Restart Codex after installing hooks or MCP config.

`install-hooks` is quiet by default: it records session starts and user prompts
without a visible hook status message. It intentionally skips per-tool hooks so
Codex Desktop does not show `Recording Codex memory` on every agent step.
Assistant final messages and detailed tool traces can still be backfilled from
transcripts with `codex-memory import`.

Use `codex-memory install-hooks --global --detailed` only if you want live
per-tool capture. Use `--include-stop` only if your Codex build handles `Stop`
hook responses cleanly.

## MCP Tools

- `memory_recent`
- `memory_search`
- `memory_by_date`
- `memory_sessions`
- `memory_get_session`
- `memory_stats`

List responses default to `format="toon"` and accept `format="json"`.

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
