from __future__ import annotations

from typing import Any

from .config import load_config
from .db import (
    connection,
    recent_records,
    records_by_range,
    search_records,
    session_records,
    sessions,
    stats,
)
from .importer import date_range
from .toon import format_payload


try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover - exercised only when dependency missing.
    FastMCP = None  # type: ignore[assignment]
    MCP_IMPORT_ERROR = exc
else:
    MCP_IMPORT_ERROR = None


def _record_types(value: list[str] | None) -> list[str] | None:
    return value or None


def _format(data: Any, fmt: str, table_name: str = "records") -> str:
    return format_payload(data, fmt, table_name)


def create_server():
    if FastMCP is None:
        raise RuntimeError(f"mcp package is not available: {MCP_IMPORT_ERROR!r}")

    mcp = FastMCP("codex-memory")

    @mcp.tool()
    def memory_recent(
        limit: int = 50,
        session_id: str | None = None,
        record_types: list[str] | None = None,
        format: str = "toon",
    ) -> str:
        """Return the most recent Codex memory records."""
        cfg = load_config()
        with connection(cfg.db_path) as conn:
            data = recent_records(
                conn,
                limit=limit,
                session_id=session_id,
                record_types=_record_types(record_types),
            )
        return _format(data, format)

    @mcp.tool()
    def memory_search(
        query: str,
        limit: int = 50,
        after: str | None = None,
        before: str | None = None,
        session_id: str | None = None,
        record_types: list[str] | None = None,
        format: str = "toon",
    ) -> str:
        """Search Codex memory by keyword and optional time/session filters."""
        cfg = load_config()
        with connection(cfg.db_path) as conn:
            data = search_records(
                conn,
                query=query,
                limit=limit,
                after=after,
                before=before,
                session_id=session_id,
                record_types=_record_types(record_types),
            )
        return _format(data, format)

    @mcp.tool()
    def memory_by_date(
        date: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 100,
        record_types: list[str] | None = None,
        format: str = "toon",
    ) -> str:
        """Return memory records for a date or explicit time range."""
        start, end = date_range(date, after, before)
        cfg = load_config()
        with connection(cfg.db_path) as conn:
            data = records_by_range(
                conn,
                after=start,
                before=end,
                limit=limit,
                record_types=_record_types(record_types),
            )
        return _format(data, format)

    @mcp.tool()
    def memory_sessions(
        query: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 20,
    ) -> str:
        """List known Codex sessions."""
        cfg = load_config()
        with connection(cfg.db_path) as conn:
            data = sessions(conn, query=query, after=after, before=before, limit=limit)
        return _format(data, "toon", "sessions")

    @mcp.tool()
    def memory_get_session(
        session_id: str,
        limit: int = 100,
        offset: int = 0,
        record_types: list[str] | None = None,
        format: str = "toon",
    ) -> str:
        """Return records for one Codex session."""
        cfg = load_config()
        with connection(cfg.db_path) as conn:
            data = session_records(
                conn,
                session_id=session_id,
                limit=limit,
                offset=offset,
                record_types=_record_types(record_types),
            )
        return _format(data, format)

    @mcp.tool()
    def memory_stats() -> str:
        """Return aggregate Codex memory statistics."""
        cfg = load_config()
        with connection(cfg.db_path) as conn:
            data = stats(conn)
        return _format(data, "json")

    return mcp


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
