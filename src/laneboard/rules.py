"""Board rules: staleness and ordering."""

from __future__ import annotations

from datetime import datetime, timedelta

from .model import Lane, utcnow

STATE_ORDER = {"blocked": 0, "error": 1, "stale": 2, "working": 3, "unknown": 4, "done": 9}


def is_stale(lane: Lane, *, stale_hours: float, now: datetime) -> bool:
    """A lane is stale when nothing about it changed for longer than the threshold.

    ``done`` lanes never go stale. Lanes with no timestamp at all are not
    stale (we cannot know) — they show up as "unknown" instead.
    """
    if (lane.status or "").lower() == "done":
        return False
    if lane.updated is None:
        return False
    hours = lane.stale_hours if lane.stale_hours is not None else stale_hours
    return now - lane.updated > timedelta(hours=hours)


def evaluate(lanes: list[Lane], *, stale_hours: float, now: datetime | None = None) -> list[Lane]:
    now = now or utcnow()
    for lane in lanes:
        lane.stale = is_stale(lane, stale_hours=stale_hours, now=now)
    return sort_lanes(lanes)


def sort_lanes(lanes: list[Lane]) -> list[Lane]:
    def key(lane: Lane):
        ts = lane.updated.timestamp() if lane.updated else 0
        return (STATE_ORDER.get(lane.state, 5), -ts, lane.name.lower())

    return sorted(lanes, key=key)


def counts(lanes: list[Lane]) -> dict[str, int]:
    out = {"blocked": 0, "stale": 0, "error": 0, "working": 0, "done": 0, "other": 0}
    for lane in lanes:
        st = lane.state
        out[st if st in out else "other"] += 1
    return out


def humanize_age(ts: datetime | None, now: datetime | None = None) -> str:
    if ts is None:
        return "unknown"
    now = now or utcnow()
    secs = int((now - ts).total_seconds())
    if secs < 0:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400 * 2:
        h, m = divmod(secs // 60, 60)
        return f"{h}h ago" if h >= 10 or m == 0 else f"{h}h {m}m ago"
    return f"{secs // 86400}d ago"
