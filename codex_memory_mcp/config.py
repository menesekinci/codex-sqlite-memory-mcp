from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


APP_DIR_NAME = "memories"
DB_FILE_NAME = "codex-memory.sqlite"
CONFIG_FILE_NAME = "codex-memory.toml"


@dataclass(frozen=True)
class MemoryConfig:
    db_path: Path
    config_path: Path
    raw_payloads: bool = False
    max_visible_chars: int = 200_000


def codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def memory_dir() -> Path:
    return codex_home() / APP_DIR_NAME


def default_db_path() -> Path:
    override = os.environ.get("CODEX_MEMORY_DB")
    if override:
        return Path(override).expanduser()
    return memory_dir() / DB_FILE_NAME


def default_config_path() -> Path:
    override = os.environ.get("CODEX_MEMORY_CONFIG")
    if override:
        return Path(override).expanduser()
    return memory_dir() / CONFIG_FILE_NAME


def load_config(config_path: Path | None = None) -> MemoryConfig:
    config_path = config_path or default_config_path()
    data: dict[str, object] = {}
    if config_path.exists():
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)

    raw_env = os.environ.get("CODEX_MEMORY_RAW_PAYLOADS")
    raw_payloads = bool(data.get("raw_payloads", False))
    if raw_env is not None:
        raw_payloads = raw_env.lower() in {"1", "true", "yes", "on"}

    max_visible_chars = int(data.get("max_visible_chars", 200_000))
    db_path_value = data.get("db_path")
    db_path = Path(str(db_path_value)).expanduser() if db_path_value else default_db_path()

    return MemoryConfig(
        db_path=db_path,
        config_path=config_path,
        raw_payloads=raw_payloads,
        max_visible_chars=max_visible_chars,
    )


def write_default_config(config_path: Path | None = None) -> Path:
    config_path = config_path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text(
            "\n".join(
                [
                    "# Codex SQLite Memory MCP configuration",
                    "raw_payloads = false",
                    "max_visible_chars = 200000",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return config_path
