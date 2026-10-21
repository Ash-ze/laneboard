"""Core data model: a *lane* is one line of work (one agent, one task, one branch...)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

KNOWN_STATUSES = ("working", "blocked", "done")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Signal:
    """One observation about a lane from one source (a status file, a git repo, a session log...)."""

    source: str  # source name, e.g. "laptop" or "gpu-box"
    kind: str  # "statusfile" | "ssh" | "git" | "claude-code" | "codex" | ...
    updated: datetime | None = None
    detail: str = ""  # short human readable line, e.g. "main, 3 uncommitted"
    error: str | None = None
    location: str = ""  # path or host:path, for display only


@dataclass
class Lane:
    name: str
    title: str = ""
    status: str = ""  # free text; working/blocked/done get special treatment
    updated: datetime | None = None
    owner: str = ""
    next: str = ""
    summary: str = ""
    host: str = ""
    signals: list[Signal] = field(default_factory=list)
    # derived by rules.evaluate()
    stale: bool = False
    stale_hours: float | None = None

    @property
    def display_title(self) -> str:
        return self.title or self.name

    @property
    def errors(self) -> list[str]:
        return [f"{s.source}: {s.error}" for s in self.signals if s.error]

    @property
    def state(self) -> str:
        """The single state used for sorting, colouring and notifications."""
        st = (self.status or "").lower()
        if st == "blocked":
            return "blocked"
        if st == "done":
            return "done"
        if self.stale:
            return "stale"
        if st == "working":
            return "working"
        if self.errors and self.updated is None:
            return "error"
        return "unknown" if not st else st

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state
        d["errors"] = self.errors

        def conv(o):
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, dict):
                return {k: conv(v) for k, v in o.items()}
            if isinstance(o, list):
                return [conv(v) for v in o]
            return o

        return conv(d)


def merge_lanes(lanes: list[Lane]) -> list[Lane]:
    """Merge lanes that share a name (e.g. a status file + its git repo + its agent session)."""
    merged: dict[str, Lane] = {}
    for lane in lanes:
        key = lane.name.strip().lower()
        cur = merged.get(key)
        if cur is None:
            merged[key] = lane
            continue
        for attr in ("title", "status", "owner", "next", "summary", "host"):
            if not getattr(cur, attr) and getattr(lane, attr):
                setattr(cur, attr, getattr(lane, attr))
        if lane.updated and (cur.updated is None or lane.updated > cur.updated):
            cur.updated = lane.updated
        cur.signals.extend(lane.signals)
    return list(merged.values())
