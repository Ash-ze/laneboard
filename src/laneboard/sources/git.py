"""Git repositories: last commit time, current branch, number of uncommitted changes.

Only read-only plumbing commands are used (``log``, ``rev-parse``, ``status --porcelain``).
``status`` is run with ``--no-optional-locks`` so it never writes the index.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..model import Lane, Signal
from . import register


def _git(repo: Path, *args: str, timeout: float = 10) -> str:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    proc = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args],
                          capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "git failed")
    return proc.stdout.decode("utf-8", "replace")


def repo_info(repo: Path) -> dict:
    info: dict = {}
    try:
        info["branch"] = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except RuntimeError:
        info["branch"] = "(no commits)"
    try:
        out = _git(repo, "log", "-1", "--format=%ct%x00%s").strip()
        ts, _, subject = out.partition("\x00")
        info["last_commit"] = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
        info["subject"] = subject
    except (RuntimeError, ValueError):
        info["last_commit"] = None
        info["subject"] = ""
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=normal")
    info["dirty"] = sum(1 for line in status.splitlines() if line.strip())
    return info


@register("git")
def collect_git(spec: dict) -> list[Lane]:
    name = spec.get("name") or "git"
    paths = spec.get("paths") or ([spec["path"]] if spec.get("path") else [])
    lanes: list[Lane] = []
    for p in paths:
        lane_name = None
        if isinstance(p, dict):
            lane_name = p.get("lane")
            p = p["path"]
        repo = Path(p).expanduser()
        lane_name = lane_name or repo.name
        try:
            if not (repo / ".git").exists():
                _git(repo, "rev-parse", "--git-dir")  # worktrees / bare-ish layouts
            info = repo_info(repo)
        except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired, NotADirectoryError) as e:
            msg = str(e)
            lanes.append(Lane(name=lane_name, signals=[Signal(source=name, kind="git", location=str(p),
                                                              error=f"not a git repo: {msg}"[:200])]))
            continue
        detail = f"{info['branch']}, {info['dirty']} uncommitted" if info["dirty"] else f"{info['branch']}, clean"
        if info.get("subject"):
            detail += f" — last: {info['subject'][:80]}"
        updated = info.get("last_commit")
        lanes.append(Lane(name=lane_name, updated=updated, host=spec.get("host", ""), signals=[
            Signal(source=name, kind="git", updated=updated, location=str(p), detail=detail)]))
    return lanes
