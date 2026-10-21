"""Push notifications when a lane *becomes* blocked or stale (ntfy or a generic webhook).

State is kept in a small JSON file so restarts do not re-send alerts.
Only transitions trigger a push: a lane that stays blocked alerts once.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from pathlib import Path

from .model import Lane

ALERT_STATES = ("blocked", "stale")


def load_state(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.get("lanes", {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def save_state(path: Path, states: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".notify-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "lanes": states}, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def transitions(lanes: list[Lane], previous: dict[str, str]) -> list[tuple[Lane, str, str]]:
    """Return (lane, old_state, new_state) for lanes entering an alert state."""
    out = []
    for lane in lanes:
        new = lane.state
        old = previous.get(lane.name, "")
        if new in ALERT_STATES and new != old:
            out.append((lane, old, new))
    return out


def format_message(lane: Lane, new: str) -> tuple[str, str]:
    title = f"[{new}] {lane.display_title}"
    parts = []
    if lane.host:
        parts.append(f"on {lane.host}")
    if lane.next:
        parts.append(f"next: {lane.next}")
    elif lane.summary:
        parts.append(lane.summary[:200])
    return title, " — ".join(parts) or new


def _post(url: str, body: bytes, headers: dict[str, str], timeout: float = 10) -> None:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (user-configured URL)
        resp.read(1024)


def send(target: dict, lane: Lane, old: str, new: str) -> bool:
    """Push one alert; returns False when this target does not want this state."""
    on = target.get("on", list(ALERT_STATES))
    if new not in on:
        return False
    title, message = format_message(lane, new)
    url = target.get("url") or os.environ.get(target.get("url_env", ""), "")
    if not url:
        raise ValueError(f"no url (env var {target.get('url_env')!r} is empty)")
    headers = {k: str(v) for k, v in (target.get("headers") or {}).items()}
    for k, env in (target.get("headers_env") or {}).items():
        if os.environ.get(env):
            headers[k] = os.environ[env]
    if target["type"] == "ntfy":
        headers.setdefault("Title", title.encode("ascii", "replace").decode())
        headers.setdefault("Priority", "high" if new == "blocked" else "default")
        headers.setdefault("Tags", "warning" if new == "blocked" else "hourglass")
        _post(url, message.encode("utf-8"), headers)
    else:
        headers.setdefault("Content-Type", "application/json")
        payload = {"event": f"lane.{new}", "lane": lane.name, "title": title, "message": message,
                   "previous_state": old or None, "state": new, "data": lane.to_dict()}
        _post(url, json.dumps(payload).encode("utf-8"), headers)
    return True


def run_notifications(lanes: list[Lane], targets: list[dict], state_path: Path) -> list[str]:
    """Send alerts for new transitions; returns log lines (errors included, never raises)."""
    previous = load_state(state_path)
    log = []
    failed: set[str] = set()
    for lane, old, new in transitions(lanes, previous):
        for t in targets:
            try:
                if send(t, lane, old, new):
                    log.append(f"sent {t['type']}: {lane.name} -> {new}")
            except Exception as e:
                failed.add(lane.name)
                log.append(f"failed {t['type']}: {lane.name} -> {new}: {e}")
    # a failed push keeps the old state, so the alert is retried next round
    state = {lane.name: (previous.get(lane.name, "") if lane.name in failed else lane.state) for lane in lanes}
    save_state(state_path, state)
    return log
