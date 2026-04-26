from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .config import load_config
from .privacy import redact_text


RECORD_TYPES = {
    "user_prompt",
    "assistant_message",
    "terminal_command",
    "terminal_output",
    "tool_call",
    "tool_output",
    "permission_request",
    "session_event",
}


@dataclass(frozen=True)
class InsertResult:
    inserted: bool
    record_id: int | None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _content_hash(
    session_id: str | None,
    turn_id: str | None,
    record_type: str,
    tool_name: str | None,
    role: str | None,
    visible_text: str,
) -> str:
    payload = {
        "session_id": session_id or "",
        "turn_id": turn_id or "",
        "record_type": record_type,
        "tool_name": tool_name or "",
        "role": role or "",
        "visible_text": visible_text,
    }
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def connect(db_path: Path | None = None, initialize: bool = True) -> sqlite3.Connection:
    cfg = load_config()
    path = db_path or cfg.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    if initialize:
        init_db(conn)
    return conn


@contextmanager
def connection(db_path: Path | None = None) -> Iterable[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY,
          started_at TEXT,
          updated_at TEXT,
          cwd TEXT,
          model TEXT,
          transcript_path TEXT,
          source TEXT
        );

        CREATE TABLE IF NOT EXISTS records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT,
          turn_id TEXT,
          ts TEXT NOT NULL,
          sequence INTEGER NOT NULL,
          record_type TEXT NOT NULL,
          tool_name TEXT,
          role TEXT,
          visible_text TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          content_hash TEXT NOT NULL,
          source_path TEXT,
          source_line INTEGER,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE SET NULL,
          UNIQUE(source_path, source_line)
        );

        CREATE TABLE IF NOT EXISTS raw_payloads (
          record_id INTEGER PRIMARY KEY,
          raw_json TEXT NOT NULL,
          FOREIGN KEY(record_id) REFERENCES records(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_records_dedupe
          ON records(session_id, coalesce(turn_id, ''), record_type, content_hash);
        CREATE INDEX IF NOT EXISTS idx_records_session_ts ON records(session_id, ts);
        CREATE INDEX IF NOT EXISTS idx_records_ts ON records(ts);
        CREATE INDEX IF NOT EXISTS idx_records_type ON records(record_type);

        CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
          visible_text,
          record_type,
          tool_name,
          content='records',
          content_rowid='id'
        );

        CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
          INSERT INTO records_fts(rowid, visible_text, record_type, tool_name)
          VALUES (new.id, new.visible_text, new.record_type, coalesce(new.tool_name, ''));
        END;
        CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
          INSERT INTO records_fts(records_fts, rowid, visible_text, record_type, tool_name)
          VALUES ('delete', old.id, old.visible_text, old.record_type, coalesce(old.tool_name, ''));
        END;
        CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
          INSERT INTO records_fts(records_fts, rowid, visible_text, record_type, tool_name)
          VALUES ('delete', old.id, old.visible_text, old.record_type, coalesce(old.tool_name, ''));
          INSERT INTO records_fts(rowid, visible_text, record_type, tool_name)
          VALUES (new.id, new.visible_text, new.record_type, coalesce(new.tool_name, ''));
        END;
        """
    )


def upsert_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    started_at: str | None = None,
    updated_at: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    transcript_path: str | None = None,
    source: str | None = None,
) -> None:
    ts = updated_at or started_at or now_iso()
    conn.execute(
        """
        INSERT INTO sessions(id, started_at, updated_at, cwd, model, transcript_path, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          started_at = coalesce(sessions.started_at, excluded.started_at),
          updated_at = max(coalesce(sessions.updated_at, ''), coalesce(excluded.updated_at, '')),
          cwd = coalesce(excluded.cwd, sessions.cwd),
          model = coalesce(excluded.model, sessions.model),
          transcript_path = coalesce(excluded.transcript_path, sessions.transcript_path),
          source = coalesce(excluded.source, sessions.source)
        """,
        (session_id, started_at or ts, ts, cwd, model, transcript_path, source),
    )


def _next_sequence(conn: sqlite3.Connection, session_id: str | None) -> int:
    row = conn.execute(
        "SELECT coalesce(max(sequence), 0) + 1 AS seq FROM records WHERE session_id IS ?",
        (session_id,),
    ).fetchone()
    return int(row["seq"])


def add_record(
    conn: sqlite3.Connection,
    *,
    session_id: str | None,
    turn_id: str | None,
    ts: str | None,
    record_type: str,
    visible_text: object,
    tool_name: str | None = None,
    role: str | None = None,
    metadata: dict[str, Any] | None = None,
    source_path: str | None = None,
    source_line: int | None = None,
    raw_json: Any | None = None,
    store_raw: bool = False,
    max_visible_chars: int = 200_000,
) -> InsertResult:
    if record_type not in RECORD_TYPES:
        raise ValueError(f"unknown record_type: {record_type}")

    redacted = redact_text(visible_text, max_visible_chars)
    if not redacted.strip() and record_type not in {"session_event", "permission_request"}:
        return InsertResult(False, None)

    if session_id:
        upsert_session(conn, session_id, updated_at=ts or now_iso())

    metadata_json = _json_dumps(metadata)
    content_hash = _content_hash(session_id, turn_id, record_type, tool_name, role, redacted)
    sequence = source_line if source_line is not None else _next_sequence(conn, session_id)

    try:
        cur = conn.execute(
            """
            INSERT INTO records(
              session_id, turn_id, ts, sequence, record_type, tool_name, role,
              visible_text, metadata_json, content_hash, source_path, source_line
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                turn_id,
                ts or now_iso(),
                sequence,
                record_type,
                tool_name,
                role,
                redacted,
                metadata_json,
                content_hash,
                source_path,
                source_line,
            ),
        )
    except sqlite3.IntegrityError:
        return InsertResult(False, None)

    record_id = int(cur.lastrowid)
    if store_raw and raw_json is not None:
        conn.execute(
            "INSERT OR REPLACE INTO raw_payloads(record_id, raw_json) VALUES (?, ?)",
            (record_id, json.dumps(raw_json, ensure_ascii=False)),
        )
    return InsertResult(True, record_id)


def _rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        item = dict(row)
        if "metadata_json" in item and not item["metadata_json"]:
            item["metadata_json"] = "{}"
        result.append(item)
    return result


def _record_type_clause(record_types: list[str] | None, params: list[Any]) -> str:
    if not record_types:
        return ""
    bad = sorted(set(record_types) - RECORD_TYPES)
    if bad:
        raise ValueError(f"unknown record_types: {', '.join(bad)}")
    params.extend(record_types)
    placeholders = ",".join("?" for _ in record_types)
    return f" AND record_type IN ({placeholders})"


def recent_records(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    session_id: str | None = None,
    record_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "WHERE 1=1"
    if session_id:
        where += " AND session_id = ?"
        params.append(session_id)
    where += _record_type_clause(record_types, params)
    params.append(max(1, min(int(limit), 500)))
    rows = conn.execute(
        f"""
        SELECT id, session_id, turn_id, ts, sequence, record_type, tool_name, role,
               visible_text, metadata_json
        FROM records
        {where}
        ORDER BY ts DESC, sequence DESC, id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return _rows_to_dicts(rows)


def _fts_query(query: str) -> str:
    tokens = re_tokens(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens[:16])


def re_tokens(query: str) -> list[str]:
    import re

    return re.findall(r"[\w.@#/\-]+", query, flags=re.UNICODE)


def search_records(
    conn: sqlite3.Connection,
    *,
    query: str,
    limit: int = 50,
    after: str | None = None,
    before: str | None = None,
    session_id: str | None = None,
    record_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "WHERE 1=1"
    if session_id:
        where += " AND records.session_id = ?"
        params.append(session_id)
    if after:
        where += " AND records.ts >= ?"
        params.append(after)
    if before:
        where += " AND records.ts < ?"
        params.append(before)
    where += _record_type_clause(record_types, params).replace("record_type", "records.record_type")

    fts = _fts_query(query)
    if fts:
        try:
            rows = conn.execute(
                f"""
                SELECT records.id, records.session_id, records.turn_id, records.ts,
                       records.sequence, records.record_type, records.tool_name,
                       records.role, records.visible_text, records.metadata_json
                FROM records_fts
                JOIN records ON records.id = records_fts.rowid
                {where} AND records_fts MATCH ?
                ORDER BY records.ts DESC, records.sequence DESC, records.id DESC
                LIMIT ?
                """,
                [*params, fts, max(1, min(int(limit), 500))],
            ).fetchall()
            return _rows_to_dicts(rows)
        except sqlite3.OperationalError:
            pass

    like = f"%{query}%"
    rows = conn.execute(
        f"""
        SELECT id, session_id, turn_id, ts, sequence, record_type, tool_name, role,
               visible_text, metadata_json
        FROM records
        {where} AND visible_text LIKE ?
        ORDER BY ts DESC, sequence DESC, id DESC
        LIMIT ?
        """,
        [*params, like, max(1, min(int(limit), 500))],
    ).fetchall()
    return _rows_to_dicts(rows)


def records_by_range(
    conn: sqlite3.Connection,
    *,
    after: str,
    before: str,
    limit: int = 100,
    record_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [after, before]
    where = "WHERE ts >= ? AND ts < ?"
    where += _record_type_clause(record_types, params)
    rows = conn.execute(
        f"""
        SELECT id, session_id, turn_id, ts, sequence, record_type, tool_name, role,
               visible_text, metadata_json
        FROM records
        {where}
        ORDER BY ts ASC, sequence ASC, id ASC
        LIMIT ?
        """,
        [*params, max(1, min(int(limit), 1000))],
    ).fetchall()
    return _rows_to_dicts(rows)


def sessions(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "WHERE 1=1"
    if query:
        where += " AND (id LIKE ? OR cwd LIKE ? OR source LIKE ?)"
        like = f"%{query}%"
        params.extend([like, like, like])
    if after:
        where += " AND updated_at >= ?"
        params.append(after)
    if before:
        where += " AND updated_at < ?"
        params.append(before)
    params.append(max(1, min(int(limit), 200)))
    rows = conn.execute(
        f"""
        SELECT id, started_at, updated_at, cwd, model, transcript_path, source
        FROM sessions
        {where}
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def session_records(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    limit: int = 100,
    offset: int = 0,
    record_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [session_id]
    where = "WHERE session_id = ?"
    where += _record_type_clause(record_types, params)
    rows = conn.execute(
        f"""
        SELECT id, session_id, turn_id, ts, sequence, record_type, tool_name, role,
               visible_text, metadata_json
        FROM records
        {where}
        ORDER BY ts ASC, sequence ASC, id ASC
        LIMIT ? OFFSET ?
        """,
        [*params, max(1, min(int(limit), 1000)), max(0, int(offset))],
    ).fetchall()
    return _rows_to_dicts(rows)


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    session_count = conn.execute("SELECT count(*) AS n FROM sessions").fetchone()["n"]
    record_count = conn.execute("SELECT count(*) AS n FROM records").fetchone()["n"]
    rows = conn.execute(
        "SELECT record_type, count(*) AS n FROM records GROUP BY record_type ORDER BY record_type"
    ).fetchall()
    first = conn.execute("SELECT min(ts) AS ts FROM records").fetchone()["ts"]
    last = conn.execute("SELECT max(ts) AS ts FROM records").fetchone()["ts"]
    return {
        "sessions": session_count,
        "records": record_count,
        "records_by_type": {row["record_type"]: row["n"] for row in rows},
        "first_ts": first,
        "last_ts": last,
    }
