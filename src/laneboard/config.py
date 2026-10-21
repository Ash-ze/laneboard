"""Configuration loading (TOML built in, YAML if PyYAML is installed)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS = {
    "title": "laneboard",
    "stale_hours": 6.0,
    "refresh_seconds": 30,
    "host": "127.0.0.1",
    "port": 8765,
    "state_file": "",
}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    board: dict = field(default_factory=lambda: dict(DEFAULTS))
    sources: list[dict] = field(default_factory=list)
    notify: list[dict] = field(default_factory=list)
    path: Path | None = None

    @property
    def stale_hours(self) -> float:
        return float(self.board.get("stale_hours", DEFAULTS["stale_hours"]))

    @property
    def title(self) -> str:
        return str(self.board.get("title", DEFAULTS["title"]))

    @property
    def refresh_seconds(self) -> int:
        return max(5, int(self.board.get("refresh_seconds", DEFAULTS["refresh_seconds"])))

    @property
    def state_file(self) -> Path:
        sf = self.board.get("state_file")
        if sf:
            return Path(sf).expanduser()
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
        return base / "laneboard" / "notify-state.json"


def candidate_paths() -> list[Path]:
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return [
        Path("laneboard.toml"),
        Path("laneboard.yaml"),
        Path("laneboard.yml"),
        xdg / "laneboard" / "config.toml",
        xdg / "laneboard" / "config.yaml",
    ]


def find_config(explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("LANEBOARD_CONFIG")
    if env:
        return Path(env).expanduser()
    for p in candidate_paths():
        if p.is_file():
            return p
    return None


def parse_config_dict(data: dict, path: Path | None = None) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("config root must be a table/mapping")
    board = dict(DEFAULTS)
    board.update(data.get("board") or {})
    sources = data.get("source") or data.get("sources") or []
    notify = data.get("notify") or []
    if isinstance(sources, dict):
        sources = [sources]
    if isinstance(notify, dict):
        notify = [notify]
    for i, s in enumerate(sources):
        if not isinstance(s, dict) or "type" not in s:
            raise ConfigError(f"source #{i + 1} needs a 'type' (local, ssh, git, claude-code, codex, aider)")
    for i, n in enumerate(notify):
        if not isinstance(n, dict) or n.get("type") not in ("ntfy", "webhook") or not (n.get("url") or n.get("url_env")):
            raise ConfigError(f"notify #{i + 1} needs type = 'ntfy' | 'webhook' and a url (or url_env)")
    return Config(board=board, sources=list(sources), notify=list(notify), path=path)


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    raw = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise ConfigError("YAML config needs PyYAML: pipx inject laneboard pyyaml") from e
        data = yaml.safe_load(raw) or {}
    else:
        try:
            data = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from e
    return parse_config_dict(data, path)
