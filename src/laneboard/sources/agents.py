"""EXPERIMENTAL: adapters that read AI coding agents' own local session logs.

These formats are private implementation details of each tool and can change
without notice. Every adapter is defensive: unknown lines are skipped, and a
file that cannot be understood still yields "last activity = file mtime".

Adapters only *read* log files, and only the tail of each file. They never
print or store more than a short summary of the last message.

Known layouts (as observed in 2025-2026; verify on your machine):

* Claude Code: ``~/.claude/projects/<encoded-cwd>/<session-id>.jsonl``.
  One JSON object per line with ``type`` (``user``/``assistant``/...),
  ``timestamp`` (ISO-8601), ``cwd`` and ``message.content`` (string or list
  of ``{"type": "text", "text": ...}`` blocks).
* Codex CLI: ``~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl``.
  Newer files wrap each line as ``{"timestamp", "type", "payload"}`` with
  ``session_meta`` (has ``cwd``), ``response_item`` (``message`` items with
  ``output_text`` blocks) and ``event_msg`` (``agent_message``/``user_message``).
  Older files have the items unwrapped. Both are handled.
* Aider: ``.aider.chat.history.md`` inside each repo (user turns start with ``#### ``).

To add another tool, subclass :class:`SessionAdapter` and register it with
:func:`register_adapter`, or expose a source via the ``laneboard.sources``
entry-point group.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..model import Lane, Signal, utcnow
from ..statusfile import parse_datetime
from . import register

TAIL_BYTES = 512 * 1024
SNIPPET = 160


@dataclass
class SessionInfo:
    path: Path
    cwd: str = ""
    last_activity: datetime | None = None
    last_role: str = ""
    last_message: str = ""


def read_tail(path: Path, nbytes: int = TAIL_BYTES) -> str:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - nbytes))
        data = fh.read()
    text = data.decode("utf-8", errors="replace")
    if size > nbytes:  # drop the partial first line
        text = text.split("\n", 1)[-1]
    return text


def read_head(path: Path, nbytes: int = 64 * 1024) -> str:
    with open(path, "rb") as fh:
        return fh.read(nbytes).decode("utf-8", errors="replace")


def _json_lines(text: str):
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def snippet(text: str, n: int = SNIPPET) -> str:
    text = re.sub(r"<[^>]{1,40}>", " ", text or "")  # strip pseudo-xml wrappers
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _blocks_text(content, accept=("text", "output_text", "input_text")) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") in accept and isinstance(b.get("text"), str):
                parts.append(b["text"])
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


class SessionAdapter:
    kind = "base"
    default_root = ""

    def session_files(self, root: Path) -> list[Path]:
        raise NotImplementedError

    def parse(self, path: Path) -> SessionInfo:
        raise NotImplementedError

    @staticmethod
    def lane_name(info: SessionInfo) -> str:
        return Path(info.cwd).name if info.cwd else info.path.stem


class ClaudeCodeAdapter(SessionAdapter):
    kind = "claude-code"
    default_root = "~/.claude/projects"

    def session_files(self, root: Path) -> list[Path]:
        return [p for p in root.glob("*/*.jsonl") if p.is_file()]

    def parse(self, path: Path) -> SessionInfo:
        info = SessionInfo(path=path)
        for obj in _json_lines(read_tail(path)):
            if obj.get("cwd"):
                info.cwd = obj["cwd"]
            ts = parse_datetime(obj.get("timestamp")) if obj.get("timestamp") else None
            if ts and (info.last_activity is None or ts > info.last_activity):
                info.last_activity = ts
            if obj.get("type") not in ("user", "assistant") or obj.get("isMeta") or obj.get("isSidechain"):
                continue
            msg = obj.get("message") or {}
            text = _blocks_text(msg.get("content"))
            if text.strip():
                info.last_role = obj["type"]
                info.last_message = snippet(text)
        if not info.cwd:  # fall back to the head of the file
            for obj in _json_lines(read_head(path)):
                if obj.get("cwd"):
                    info.cwd = obj["cwd"]
                    break
        return info


class CodexAdapter(SessionAdapter):
    kind = "codex"
    default_root = "~/.codex/sessions"

    def session_files(self, root: Path) -> list[Path]:
        return [p for p in root.rglob("rollout-*.jsonl") if p.is_file()]

    @staticmethod
    def _handle_item(info: SessionInfo, item: dict) -> None:
        t = item.get("type")
        if t in ("session_meta", "turn_context") or t is None:
            if item.get("cwd"):
                info.cwd = item["cwd"]
        elif t == "message" and item.get("role") in ("assistant", "user"):
            text = _blocks_text(item.get("content"))
            if item["role"] == "user" and "<environment_context>" in text:
                m = re.search(r"<cwd>(.*?)</cwd>", text)
                if m and not info.cwd:
                    info.cwd = m.group(1).strip()
                return
            if text.strip():
                info.last_role, info.last_message = item["role"], snippet(text)
        elif t in ("agent_message", "user_message") and isinstance(item.get("message"), str):
            if item["message"].strip():
                info.last_role = "assistant" if t == "agent_message" else "user"
                info.last_message = snippet(item["message"])

    def parse(self, path: Path) -> SessionInfo:
        info = SessionInfo(path=path)
        for chunk in (read_head(path), read_tail(path)):
            for obj in _json_lines(chunk):
                ts = parse_datetime(obj.get("timestamp")) if obj.get("timestamp") else None
                if ts and (info.last_activity is None or ts > info.last_activity):
                    info.last_activity = ts
                if isinstance(obj.get("payload"), dict):
                    item = dict(obj["payload"])
                    if obj.get("type") in ("session_meta", "turn_context"):
                        item["type"] = obj["type"]
                    self._handle_item(info, item)
                else:
                    self._handle_item(info, obj)
        return info


ADAPTERS: dict[str, SessionAdapter] = {}


def register_adapter(adapter: SessionAdapter) -> SessionAdapter:
    ADAPTERS[adapter.kind] = adapter
    register(adapter.kind)(lambda spec, _a=adapter: collect_sessions(_a, spec))
    return adapter


def collect_sessions(adapter: SessionAdapter, spec: dict, *, now: datetime | None = None) -> list[Lane]:
    name = spec.get("name") or adapter.kind
    root = Path(spec.get("path") or adapter.default_root).expanduser()
    if not root.is_dir():
        if spec.get("optional", True):
            return []
        return [Lane(name=name, signals=[Signal(source=name, kind=adapter.kind, location=str(root),
                                                error="log directory not found")])]
    now = now or utcnow()
    max_age = timedelta(days=float(spec.get("max_age_days", 3)))
    files = []
    for f in adapter.session_files(root):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if now - mtime <= max_age:
            files.append((mtime, f))
    files.sort(reverse=True)
    files = files[: int(spec.get("max_sessions", 20))]
    only = {s.lower() for s in spec.get("lanes", [])}
    best: dict[str, tuple[SessionInfo, datetime]] = {}
    for mtime, f in files:
        try:
            info = adapter.parse(f)
        except OSError:
            continue
        when = info.last_activity or mtime
        lane = adapter.lane_name(info)
        if only and lane.lower() not in only:
            continue
        if lane not in best or when > best[lane][1]:
            best[lane] = (info, when)
    lanes = []
    for lane, (info, when) in best.items():
        who = {"assistant": "agent", "user": "you"}.get(info.last_role, info.last_role)
        detail = f"{who}: {info.last_message}" if info.last_message else "session active"
        lanes.append(Lane(name=lane, updated=when, host=spec.get("host", ""), signals=[
            Signal(source=name, kind=adapter.kind, updated=when, detail=detail,
                   location=f"{adapter.kind} session {info.path.stem[:8]}")]))
    return lanes


register_adapter(ClaudeCodeAdapter())
register_adapter(CodexAdapter())


@register("aider")
def collect_aider(spec: dict) -> list[Lane]:
    """Aider keeps ``.aider.chat.history.md`` in the repo; user turns start with ``#### ``."""
    name = spec.get("name") or "aider"
    lanes = []
    for p in spec.get("paths") or ([spec["path"]] if spec.get("path") else []):
        repo = Path(p).expanduser()
        hist = repo / spec.get("history_file", ".aider.chat.history.md")
        if not hist.is_file():
            continue
        mtime = datetime.fromtimestamp(hist.stat().st_mtime, tz=timezone.utc)
        last = ""
        for line in read_tail(hist, 64 * 1024).splitlines():
            if line.startswith("#### "):
                last = line[5:]
        lanes.append(Lane(name=repo.name, updated=mtime, signals=[
            Signal(source=name, kind="aider", updated=mtime, location=str(hist),
                   detail=f"you: {snippet(last)}" if last else "aider session")]))
    return lanes
