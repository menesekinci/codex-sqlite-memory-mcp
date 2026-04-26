<img width="1672" height="941" alt="ChatGPT Image 26 Nis 2026 04_48_05" src="https://github.com/user-attachments/assets/c73148df-9b73-4a1f-95a0-493fd87393d3" />

# Codex SQLite Memory MCP

Local-first long-term memory for Codex sessions, backed by SQLite and exposed through MCP.

It records Codex hook events, imports existing Codex JSONL history, redacts visible secrets by default, and lets Codex search previous sessions through memory tools.

## Why

Codex is great inside one session, but useful context often disappears between sessions:

- previous project decisions
- commands that fixed an issue
- terminal output from old debugging sessions
- earlier prompts and assistant responses
- session-level storage and token usage

This package turns local Codex history into searchable memory.

## Features

- SQLite-backed local memory
- one-command setup with `codex-memory setup`
- Codex hook capture
- existing history/session import
- MCP tools for search, recent records, sessions, stats, session info, and deletion
- quiet hooks by default
- optional detailed capture for tool calls and terminal output
- FTS5 full-text search
- visible secret redaction by default
- raw payload storage disabled by default
- optional token counting with `tiktoken`

## Quick Start

```powershell
py -m pip install -e .[dev]
codex-memory setup --global
```

Restart Codex after setup.

`setup` creates the config/database, imports existing Codex history, installs quiet hooks, enables Codex hooks, and registers the MCP server.

For macOS/Linux, use `python` instead of `py`:

```bash
python -m pip install -e .[dev]
codex-memory setup --global
```

Optional token support:

```bash
python -m pip install -e .[dev,tokens]
```

## CLI

```powershell
codex-memory setup --global
codex-memory import
codex-memory recent --limit 20
codex-memory search "sqlite migration"
codex-memory session-info <session-id>
codex-memory delete-session <session-id> --yes
codex-memory stats
codex-memory uninstall --global
```

Useful setup flags:

```powershell
codex-memory setup --global --skip-import
codex-memory setup --global --detailed
codex-memory setup --global --no-stop
```

Use `--detailed` only when you want live per-tool capture. The default mode records session starts, user prompts, and assistant final messages without noisy per-step hook status messages.

## MCP Tools

After `setup`, Codex can use these tools:

- `memory_recent` - latest records
- `memory_search` - keyword search with optional filters
- `memory_by_date` - records for a date or time range
- `memory_sessions` - known Codex sessions
- `memory_get_session` - records from one session
- `memory_session_info` - record counts, estimated storage, and token usage
- `memory_delete_session` - delete a session and its records; requires `confirm=true`
- `memory_stats` - aggregate database stats

List-style responses default to `format="toon"`; most tools also accept `format="json"`.

Example prompts for Codex:

```text
Search my memory for the last discussion about the SQLite schema.
```

```text
Find previous terminal output mentioning pytest failures.
```

```text
Show session info for this session before deleting it.
```

## Storage

Default database:

```text
~/.codex/memories/codex-memory.sqlite
```

Default config:

```text
~/.codex/memories/codex-memory.toml
```

Default config:

```toml
raw_payloads = false
max_visible_chars = 200000
```

Environment overrides:

- `CODEX_HOME`
- `CODEX_MEMORY_DB`
- `CODEX_MEMORY_CONFIG`
- `CODEX_MEMORY_RAW_PAYLOADS`

## Privacy

The default behavior is conservative:

- memory stays local in SQLite
- visible text is redacted before storage
- raw hook payloads are not stored unless `raw_payloads = true`
- long records are truncated by `max_visible_chars`
- edit/write/apply_patch-style tool content is summarized instead of storing full patches

The redactor covers common token/password/API-key patterns, but it is not a formal security boundary. Avoid pasting secrets into Codex if you do not want them stored anywhere.

## Troubleshooting

Codex does not see the tools:

```powershell
codex-memory setup --global
```

Then restart Codex.

Hooks are not recording:

```powershell
codex-memory stats
```

Hook failures are fail-open and logged at:

```text
~/.codex/memories/codex-memory-hook.log
```

## Development

```powershell
py -m pip install -e .[dev]
pytest
```

Package metadata is in `pyproject.toml`.

## Roadmap

- semantic search
- memory summaries/facts layer
- purge/export commands
- encrypted database option
- PyPI publishing
- CI workflow

## License

Add a license before wider adoption. MIT or Apache-2.0 are good defaults.
