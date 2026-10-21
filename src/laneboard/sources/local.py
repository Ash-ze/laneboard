"""Local status files: every matching Markdown file in a directory is one lane."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..model import Lane, Signal
from ..statusfile import parse_status_text
from . import register

MAX_BYTES = 256 * 1024


def lane_from_text(text: str, *, stem: str, source: str, kind: str, location: str,
                   mtime: datetime | None, host: str = "") -> Lane:
    doc = parse_status_text(text, mtime=mtime)
    return Lane(
        name=doc.lane or stem,
        title=doc.title,
        status=doc.status,
        updated=doc.updated,
        owner=doc.owner,
        next=doc.next,
        summary=doc.summary,
        host=host,
        signals=[Signal(source=source, kind=kind, updated=doc.updated, location=location,
                        detail=f"status file ({doc.status or 'no status'})")],
    )


def read_text_limited(path: Path) -> str:
    with open(path, "rb") as fh:
        data = fh.read(MAX_BYTES)
    return data.decode("utf-8", errors="replace")


@register("local")
def collect_local(spec: dict) -> list[Lane]:
    name = spec.get("name") or "local"
    paths = spec.get("paths") or ([spec["path"]] if spec.get("path") else [])
    pattern = spec.get("pattern", "*.md")
    host = spec.get("host", "")
    lanes: list[Lane] = []
    for p in paths:
        base = Path(p).expanduser()
        if base.is_file():
            files = [base]
        elif base.is_dir():
            files = sorted(f for f in base.glob(pattern) if f.is_file())
        else:
            lanes.append(Lane(name=f"{name}:{base.name}", host=host, signals=[
                Signal(source=name, kind="statusfile", location=str(p), error="path not found")]))
            continue
        for f in files:
            if f.name.lower() in ("readme.md",) and not spec.get("include_readme"):
                continue
            try:
                text = read_text_limited(f)
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            except OSError as e:
                lanes.append(Lane(name=f.stem, host=host, signals=[
                    Signal(source=name, kind="statusfile", location=str(f), error=str(e))]))
                continue
            lanes.append(lane_from_text(text, stem=f.stem, source=name, kind="statusfile",
                                        location=str(p if base.is_file() else Path(p) / f.name),
                                        mtime=mtime, host=host))
    return lanes
