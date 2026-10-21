from datetime import datetime, timezone

from laneboard.statusfile import parse_datetime, parse_front_matter, parse_status_text

MTIME = datetime(2026, 4, 30, 8, 0, tzinfo=timezone.utc)


def test_full_front_matter():
    doc = parse_status_text("""---
status: Blocked
updated: 2026-05-01T10:30:00Z
owner: "codex@gpu-box"
next: waiting for review   # not a comment, kept as text
lane: api-rate-limits
---
# API rate limits

First paragraph line one
continues here.

Second paragraph.
""", mtime=MTIME)
    assert doc.status == "blocked"
    assert doc.updated == datetime(2026, 5, 1, 10, 30, tzinfo=timezone.utc)
    assert doc.owner == "codex@gpu-box"
    assert doc.next.startswith("waiting for review")
    assert doc.lane == "api-rate-limits"
    assert doc.title == "API rate limits"
    assert doc.summary == "First paragraph line one continues here."


def test_no_front_matter_uses_mtime_and_first_paragraph():
    doc = parse_status_text("# Flaky CI\n\n- bisecting the timeout\n- next: seed\n\nmore", mtime=MTIME)
    assert doc.status == ""
    assert doc.updated == MTIME
    assert doc.title == "Flaky CI"
    assert doc.summary == "bisecting the timeout next: seed"


def test_plain_text_without_heading():
    doc = parse_status_text("just a note\n", mtime=MTIME)
    assert doc.title == "" and doc.summary == "just a note"


def test_status_aliases_and_unknown_values_are_kept():
    assert parse_status_text("---\nstatus: WIP\n---\n").status == "working"
    assert parse_status_text("---\nstatus: waiting\n---\n").status == "blocked"
    assert parse_status_text("---\nstatus: merged\n---\n").status == "done"
    assert parse_status_text("---\nstatus: reviewing\n---\n").status == "reviewing"


def test_front_matter_edge_cases():
    text = "﻿---\r\nstatus: done\r\nnext: >\r\n  multi\r\n  line\r\n---\r\nBody text\r\n"
    doc = parse_status_text(text)
    assert doc.status == "done"
    assert doc.next == "multi line"
    assert doc.summary == "Body text"


def test_front_matter_only_simple_subset():
    fm = parse_front_matter("a: 1\n# comment\nb: 'two'\nlist:\n  - x\n  - y\n!!python/object: boom\n")
    assert fm == {"a": "1", "b": "two", "list": "x y"}


def test_unterminated_front_matter_is_body():
    doc = parse_status_text("---\nstatus: blocked\nno end marker\n", mtime=MTIME)
    assert doc.status == ""


def test_code_blocks_skipped_and_summary_truncated():
    doc = parse_status_text("```\ncode\n```\n" + "word " * 200)
    assert "code" not in doc.summary
    assert len(doc.summary) <= 280 and doc.summary.endswith("…")


def test_bad_updated_falls_back_to_mtime():
    assert parse_status_text("---\nupdated: yesterday-ish\n---\n", mtime=MTIME).updated == MTIME


def test_parse_datetime_variants():
    utc = timezone.utc
    assert parse_datetime("2026-05-01") is not None
    assert parse_datetime("2026-05-01 14:30+02:00") == datetime(2026, 5, 1, 12, 30, tzinfo=utc)
    assert parse_datetime("2026-05-01T14:30:00.123Z").tzinfo == utc
    assert parse_datetime("1777636800") == datetime(2026, 5, 1, 12, 0, tzinfo=utc)
    assert parse_datetime("") is None and parse_datetime("nope") is None
