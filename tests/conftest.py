import os
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

FAKE_SSH = textwrap.dedent(r'''
    #!/usr/bin/env python3
    # Stand-in for ssh: host H maps to directory <root>/H, the remote command runs in `sh -c`.
    import os, subprocess, sys, time
    args = sys.argv[1:]
    root = args[1]; args = args[2:]          # --root DIR
    log = os.path.join(root, "calls.log")
    with open(log, "a") as fh:
        fh.write(repr(args) + "\n")
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-o", "-p", "-i", "-F", "-J", "-l") else 1
    host, cmd = args[i], " ".join(args[i + 1:])
    if host == "slow-host":
        time.sleep(5)
    if host == "auth-fail":
        sys.stderr.write("user@auth-fail: Permission denied (publickey).\n"); sys.exit(255)
    home = os.path.join(root, host)
    if not os.path.isdir(home):
        sys.stderr.write(f"ssh: Could not resolve hostname {host}: Name or service not known\n"); sys.exit(255)
    sys.exit(subprocess.run(["sh", "-c", cmd], cwd=home,
                            env={"HOME": home, "PATH": os.environ.get("PATH", "")}).returncode)
''').lstrip()


@pytest.fixture
def fake_ssh(tmp_path):
    """Return (ssh_command, root): root/<host>/ is that host's $HOME."""
    root = tmp_path / "remote"
    root.mkdir()
    script = tmp_path / "fake_ssh.py"
    script.write_text(FAKE_SSH)
    return [sys.executable, str(script), "--root", str(root)], root


def write(path: Path, text: str, mtime: datetime | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())
    if mtime:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path
