"""Parser for laneboard status files.

A status file is plain Markdown with an optional front-matter block::

    ---
    status: blocked
    updated: 2026-05-01T14:30:00Z
    owner: codex@gpu-box
    next: waiting for review of the migration plan
    ---
    # Frontend refactor

    Moving the settings pages to the new form components. Two of five done.

Every field is optional. Without front-matter, the file modification time
is used as ``updated`` and the first paragraph becomes the summary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

FRONT_MATTER_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S)
SUMMARY_MAX = 280

STATUS_ALIASES = {
    "wip": "working",
    "active": "working",
    "in-progress": "working",
    "in_progress": "working",
    "running": "working",
    "stuck": "blocked",
    "waiting": "blocked",
    "needs-input": "blocked",
    "finished": "done",
    "complete": "done",
    "completed": "done",
    "merged": "done",
}


@dataclass
class StatusDoc:
    fields: dict[str, str] = field(default_factory=dict)
    title: str = ""
    summary: str = ""
    status: str = ""
    updated: datetime | None = None
    owner: str = ""
    next: str = ""
    lane: str = ""


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def parse_front_matter(block: str) -> dict[str, str]:
    """Parse a deliberately small YAML subset: ``key: value`` lines, ``#`` comments.

    Folded/list values are flattened to a single line. We do not depend on PyYAML
    so a status file can never execute anything or pull in a parser surprise.
    """
    out: dict[str, str] = {}
    last: str | None = None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", line)
        if m and not raw[:1].isspace():
            key = m.group(1).strip().lower()
            val = m.group(2)
            if val.strip() in ("|", ">", "|-", ">-"):
                val = ""
            out[key] = _unquote(val)
            last = key
        elif last is not None and raw[:1].isspace():
            piece = line.strip()
            if piece.startswith("- "):
                piece = piece[2:]
            out[last] = (out[last] + " " + _unquote(piece)).strip()
    return out


def parse_datetime(value: str | None, *, default_tz=None) -> datetime | None:
    """Parse ISO-8601-ish timestamps (``2026-05-01``, ``2026-05-01 14:30``, ``...Z``) or epoch seconds."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time(0, 0))
    else:
        s = str(value).strip()
        if not s:
            return None
        if re.fullmatch(r"\d{9,11}(\.\d+)?", s):
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
        s = s.replace("Z", "+00:00").replace("z", "+00:00")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?.*", s):
            s = s.replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz) if default_tz else dt.astimezone()
    return dt.astimezone(timezone.utc)


def normalize_status(value: str) -> str:
    v = (value or "").strip().lower()
    return STATUS_ALIASES.get(v, v)


def _first_paragraph(body: str) -> tuple[str, str]:
    """Return (title, summary). Title = first ATX heading; summary = first prose paragraph."""
    title = ""
    para: list[str] = []
    in_code = False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            in_code = not in_code
            if para:
                break
            continue
        if in_code:
            continue
        if s.startswith("#"):
            if para:
                break
            if not title:
                title = s.lstrip("#").strip()
            continue
        if not s:
            if para:
                break
            continue
        if s.startswith(("<!--", "---", "***")):
            continue
        para.append(re.sub(r"^([-*+]|\d+\.)\s+", "", s))
    summary = " ".join(para)
    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) > SUMMARY_MAX:
        summary = summary[: SUMMARY_MAX - 1].rstrip() + "…"
    return title, summary


def parse_status_text(text: str, *, mtime: datetime | None = None) -> StatusDoc:
    fields: dict[str, str] = {}
    body = text
    m = FRONT_MATTER_RE.match(text)
    if m:
        fields = parse_front_matter(m.group(1))
        body = text[m.end():]
    title, summary = _first_paragraph(body)
    doc = StatusDoc(fields=fields)
    doc.title = fields.get("title") or title
    doc.summary = fields.get("summary") or summary
    doc.status = normalize_status(fields.get("status", ""))
    doc.owner = fields.get("owner", "")
    doc.next = fields.get("next", "")
    doc.lane = fields.get("lane", "")
    doc.updated = parse_datetime(fields.get("updated")) or (mtime.astimezone(timezone.utc) if mtime else None)
    return doc
