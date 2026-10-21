"""Remote status files over the user's own ``ssh`` binary.

laneboard never stores keys, passwords or host details: it runs
``ssh <host> <read-only shell snippet>`` exactly as you would by hand, so
your ``~/.ssh/config`` (aliases, ProxyJump, agent, ControlMaster) applies.
The remote snippet only uses ``stat`` and ``cat`` on the paths you list.
"""

from __future__ import annotations

import re
import secrets
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import PurePosixPath

from ..model import Lane, Signal
from .local import MAX_BYTES, lane_from_text
from . import register

SAFE_GLOB = re.compile(r"^[A-Za-z0-9._*?\-\[\]]+$")
SAFE_HOST = re.compile(r"^[A-Za-z0-9_.@:\[\]\-]+$")


def _remote_dir(d: str) -> str:
    if d in ("~", ""):
        return '"$HOME"'
    if d.startswith("~/"):
        return '"$HOME"/' + shlex.quote(d[2:])
    return shlex.quote(d)


def build_remote_command(path: str, marker: str, with_mtime: bool = True) -> str:
    """Build a POSIX-sh snippet that prints every file matching ``path`` behind a marker line."""
    p = PurePosixPath(path)
    base = p.name
    parent = str(p.parent) if str(p.parent) != "." else ""
    if path.startswith("~/") and parent == "~":
        parent = "~"
    if any(c in base for c in "*?["):
        if not SAFE_GLOB.match(base):
            raise ValueError(f"unsupported characters in glob: {base!r}")
        target = f"{_remote_dir(parent)}/{base}" if parent else base
    else:
        target = f"{_remote_dir(parent)}/{shlex.quote(base)}" if parent else shlex.quote(base)
    mt = ('$(stat -c %Y -- "$f" 2>/dev/null || stat -f %m -- "$f" 2>/dev/null || echo 0)'
          if with_mtime else "0")
    return (f'for f in {target}; do [ -f "$f" ] || continue; '
            f'printf "\\n{marker} %s %s\\n" "{mt}" "$f"; head -c {MAX_BYTES} -- "$f"; done')


def parse_remote_output(out: str, marker: str) -> list[tuple[str, datetime | None, str]]:
    """Split output into (remote_path, mtime, text) triples."""
    items: list[tuple[str, datetime | None, str]] = []
    pattern = re.compile(rf"^{re.escape(marker)} (\d+) (.*)$", re.M)
    matches = list(pattern.finditer(out))
    for i, m in enumerate(matches):
        start = m.end() + 1
        end = matches[i + 1].start() - 1 if i + 1 < len(matches) else len(out)
        secs = int(m.group(1))
        mtime = datetime.fromtimestamp(secs, tz=timezone.utc) if secs > 0 else None
        items.append((m.group(2), mtime, out[start:max(start, end)]))
    return items


@register("ssh")
def collect_ssh(spec: dict) -> list[Lane]:
    host = spec.get("host")
    if not host or not SAFE_HOST.match(host) or host.startswith("-"):
        raise ValueError("ssh source needs a plain 'host' (an alias from your ~/.ssh/config)")
    name = spec.get("name") or host
    paths = spec.get("paths") or ([spec["path"]] if spec.get("path") else [])
    timeout = float(spec.get("timeout", 15))
    ssh_cmd = spec.get("ssh_command") or ["ssh"]
    if isinstance(ssh_cmd, str):
        ssh_cmd = shlex.split(ssh_cmd)
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={int(max(1, timeout))}"]
    opts += list(spec.get("ssh_options", []))
    lanes: list[Lane] = []
    for path in paths:
        marker = "@@laneboard-" + secrets.token_hex(6)
        remote = build_remote_command(path, marker, with_mtime=spec.get("remote_mtime", True))
        loc = f"{host}:{path}"
        try:
            proc = subprocess.run([*ssh_cmd, *opts, host, remote], capture_output=True,
                                  timeout=timeout + min(5.0, timeout), stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            lanes.append(_error_lane(name, host, path, f"ssh binary not found: {ssh_cmd[0]}"))
            continue
        except subprocess.TimeoutExpired:
            lanes.append(_error_lane(name, host, path, f"timed out after {timeout:.0f}s"))
            continue
        out = proc.stdout.decode("utf-8", errors="replace")
        items = parse_remote_output(out, marker)
        if proc.returncode != 0 and not items:
            err = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            lanes.append(_error_lane(name, host, path, (err[-1] if err else f"ssh exited {proc.returncode}")[:200]))
            continue
        if not items:
            lanes.append(_error_lane(name, host, path, "no matching file on remote"))
            continue
        for rpath, mtime, text in items:
            stem = PurePosixPath(rpath).stem
            lanes.append(lane_from_text(text, stem=stem, source=name, kind="ssh", location=loc,
                                        mtime=mtime, host=spec.get("label", host)))
    return lanes


def _error_lane(name: str, host: str, path: str, msg: str) -> Lane:
    stem = PurePosixPath(path).stem
    if any(c in stem for c in "*?["):
        stem = f"{host}"
    return Lane(name=stem, host=host, signals=[Signal(source=name, kind="ssh", location=f"{host}:{path}", error=msg)])
