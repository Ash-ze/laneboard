from datetime import timedelta

from conftest import NOW
from laneboard.model import Lane, Signal, merge_lanes
from laneboard.rules import counts, evaluate, humanize_age, is_stale


def lane(name, status="", age_h=None, **kw):
    return Lane(name=name, status=status, updated=None if age_h is None else NOW - timedelta(hours=age_h), **kw)


def test_stale_threshold_is_strict():
    assert not is_stale(lane("a", "working", 6), stale_hours=6, now=NOW)
    assert is_stale(lane("a", "working", 6.01), stale_hours=6, now=NOW)


def test_done_never_stale_and_unknown_time_not_stale():
    assert not is_stale(lane("a", "done", 100), stale_hours=6, now=NOW)
    assert not is_stale(lane("a", "working"), stale_hours=6, now=NOW)


def test_per_lane_override():
    ln = lane("a", "working", 10)
    ln.stale_hours = 12
    assert not is_stale(ln, stale_hours=6, now=NOW)


def test_state_precedence_and_sorting():
    lanes = evaluate([
        lane("done-one", "done", 1),
        lane("fresh", "working", 1),
        lane("old", "working", 30),
        lane("blocked-old", "blocked", 30),
        lane("nostatus", "", 2),
        Lane(name="broken", signals=[Signal(source="s", kind="ssh", error="boom")]),
    ], stale_hours=6, now=NOW)
    assert [ln.name for ln in lanes] == ["blocked-old", "broken", "old", "fresh", "nostatus", "done-one"]
    assert lanes[0].state == "blocked" and lanes[0].stale  # blocked wins over stale
    assert lanes[1].state == "error"
    assert counts(lanes) == {"blocked": 1, "stale": 1, "error": 1, "working": 1, "done": 1, "other": 1}


def test_merge_by_name_keeps_first_status_and_newest_time():
    a = lane("Frontend", "working", 5, owner="claude-code", signals=[Signal("laptop", "statusfile")])
    b = lane("frontend", "", 1, signals=[Signal("git", "git")])
    (m,) = merge_lanes([a, b])
    assert m.status == "working" and m.owner == "claude-code"
    assert m.updated == NOW - timedelta(hours=1)
    assert [s.kind for s in m.signals] == ["statusfile", "git"]


def test_humanize():
    assert humanize_age(None) == "unknown"
    assert humanize_age(NOW - timedelta(seconds=5), NOW) == "5s ago"
    assert humanize_age(NOW - timedelta(minutes=5), NOW) == "5m ago"
    assert humanize_age(NOW - timedelta(hours=2, minutes=5), NOW) == "2h 5m ago"
    assert humanize_age(NOW - timedelta(hours=20), NOW) == "20h ago"
    assert humanize_age(NOW - timedelta(days=3), NOW) == "3d ago"
    assert humanize_age(NOW + timedelta(minutes=1), NOW) == "just now"
