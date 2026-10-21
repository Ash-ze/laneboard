#!/usr/bin/env python3
"""Build a self-contained demo board with fictional lanes.

    python examples/demo/make_demo.py /tmp/laneboard-demo
    laneboard -c /tmp/laneboard-demo/laneboard.toml serve

Everything is fake and local: "remote" machines are directories served by a
stand-in ``ssh`` script, agent session logs are generated, git repos are
created on the fly. Timestamps are relative to *now* so the demo always shows
a mix of fresh, stale and blocked lanes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOW = datetime.now(timezone.utc)


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def write(path: Path, text: str, mtime: datetime | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    if mtime:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))


FAKE_SSH = r'''#!/usr/bin/env python3
"""Stand-in for `ssh` used by the demo: host "<name>" is the directory <root>/<name>."""
import os, subprocess, sys
args = sys.argv[1:]
root = "."
if args[:1] == ["--root"]:
    root, args = args[1], args[2:]
i = 0
while i < len(args) and args[i].startswith("-"):
    i += 2 if args[i] in ("-o", "-p", "-i", "-F", "-J", "-l") else 1
host, cmd = args[i], " ".join(args[i + 1:])
home = os.path.join(root, host)
if not os.path.isdir(home):
    sys.stderr.write(f"ssh: Could not resolve hostname {host}: Name or service not known\n")
    sys.exit(255)
sys.exit(subprocess.run(["sh", "-c", cmd], cwd=home, env={"HOME": home, "PATH": os.environ.get("PATH", "")}).returncode)
'''


def git(repo: Path, *args: str, when: datetime | None = None) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="demo", GIT_AUTHOR_EMAIL="demo@example.invalid",
               GIT_COMMITTER_NAME="demo", GIT_COMMITTER_EMAIL="demo@example.invalid",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = iso(when)
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)


def make_repo(repo: Path, commits: list[tuple[str, datetime]], dirty: int, branch: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", branch)
    for i, (msg, when) in enumerate(commits):
        (repo / f"file{i}.txt").write_text(msg + "\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", msg, when=when)
    for i in range(dirty):
        (repo / f"wip{i}.txt").write_text("work in progress\n")


def jsonl(path: Path, rows: list[dict], mtime: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))


def main(out: Path) -> None:
    out = out.resolve()
    lanes = out / "lanes"
    write(lanes / "frontend-refactor.md", f"""
        ---
        status: working
        updated: {iso(ago(minutes=12))}
        owner: claude-code
        next: convert the settings forms, then run visual regression
        ---
        # Frontend refactor

        Moving the settings pages to the shared form components. 3 of 5 pages done,
        typecheck is green.
        """)
    write(lanes / "api-rate-limits.md", f"""
        ---
        status: blocked
        updated: {iso(ago(hours=1, minutes=40))}
        owner: codex
        next: decide token bucket vs sliding window (see notes in PR draft)
        ---
        # API rate limits

        Prototype of per-key limits is done; both strategies are implemented behind a flag.
        Needs a human decision before continuing.
        """)
    write(lanes / "flaky-ci-hunt.md", """
        # Flaky CI hunt

        Bisecting the intermittent timeout in the integration suite. Narrowed it down to
        the cache warm-up step; trying to reproduce with a fixed seed.
        """, mtime=ago(hours=9, minutes=20))
    write(lanes / "docs-site-migration.md", f"""
        ---
        status: done
        updated: {iso(ago(days=2, hours=3))}
        owner: aider
        ---
        # Docs site migration

        All pages moved to the new generator, redirects verified.
        """)

    remote = out / "remote"
    write(remote / "gpu-box" / "work" / "lanes" / "paper-experiments.md", f"""
        ---
        status: working
        updated: {iso(ago(minutes=48))}
        owner: codex
        next: ablation run 4/6, then regenerate the results table
        ---
        # Paper experiments

        Running the ablation grid on the second dataset. Runs 1-3 finished and match the
        baseline within noise.
        """)
    write(remote / "gpu-box" / "work" / "lanes" / "dataset-cleanup.md", f"""
        ---
        status: working
        updated: {iso(ago(hours=20))}
        owner: gemini-cli
        next: dedupe pass on the remaining shards
        ---
        # Dataset cleanup

        Normalised labels for shards 1-8. Last run stopped without a summary.
        """)
    (out / "fake_ssh.py").write_text(FAKE_SSH, encoding="utf-8")

    repos = out / "repos"
    make_repo(repos / "frontend-refactor", [("scaffold form components", ago(days=1)),
                                             ("port profile page", ago(hours=3)),
                                             ("port billing page", ago(minutes=35))], dirty=3,
              branch="refactor/settings-forms")
    make_repo(repos / "flaky-ci-hunt", [("add retry logging", ago(hours=10))], dirty=0, branch="ci/flaky-timeout")

    demo_root = "/srv/demo/projects"
    jsonl(out / "claude" / "projects" / "-srv-demo-projects-frontend-refactor" / "session-a1.jsonl", [
        {"type": "user", "cwd": f"{demo_root}/frontend-refactor", "timestamp": iso(ago(minutes=20)),
         "message": {"role": "user", "content": "Port the notifications page next."}},
        {"type": "assistant", "cwd": f"{demo_root}/frontend-refactor", "timestamp": iso(ago(minutes=4)),
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Notifications page ported; running the typecheck before moving on."}]}},
    ], ago(minutes=4))
    jsonl(out / "codex" / "sessions" / NOW.strftime("%Y/%m/%d") / "rollout-demo-0001.jsonl", [
        {"timestamp": iso(ago(hours=2)), "type": "session_meta",
         "payload": {"id": "demo-0001", "cwd": f"{demo_root}/api-rate-limits"}},
        {"timestamp": iso(ago(hours=1, minutes=41)), "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": [
             {"type": "output_text", "text": "Both strategies pass the load test. Which one should be the default?"}]}},
    ], ago(hours=1, minutes=41))

    py = sys.executable
    write(out / "laneboard.toml", f"""
        [board]
        title = "Agent lanes (demo)"
        stale_hours = 6
        refresh_seconds = 15
        state_file = "{out / 'notify-state.json'}"

        [[source]]
        type = "local"
        name = "laptop"
        path = "{lanes}"
        host = "laptop"

        [[source]]
        type = "ssh"
        host = "gpu-box"
        paths = ["~/work/lanes/*.md"]
        ssh_command = ["{py}", "{out / 'fake_ssh.py'}", "--root", "{remote}"]

        [[source]]
        type = "ssh"
        name = "old-laptop"
        host = "old-laptop"
        paths = ["~/lanes/side-project.md"]
        ssh_command = ["{py}", "{out / 'fake_ssh.py'}", "--root", "{remote}"]

        [[source]]
        type = "git"
        paths = ["{repos / 'frontend-refactor'}", "{repos / 'flaky-ci-hunt'}"]

        [[source]]
        type = "claude-code"
        path = "{out / 'claude' / 'projects'}"

        [[source]]
        type = "codex"
        path = "{out / 'codex' / 'sessions'}"
        """)
    print(out / "laneboard.toml")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "laneboard-demo"))
