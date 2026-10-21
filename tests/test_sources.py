import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from conftest import write
from laneboard.sources import collect
from laneboard.sources.agents import ClaudeCodeAdapter, CodexAdapter, collect_sessions
from laneboard.sources.local import collect_local
from laneboard.sources.ssh import build_remote_command, collect_ssh, parse_remote_output

T0 = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- local

def test_local_directory(tmp_path):
    d = tmp_path / "lanes"
    write(d / "frontend-refactor.md", "---\nstatus: working\nowner: claude-code\n---\n# Frontend\n\nDoing it.\n")
    write(d / "flaky-ci.md", "Plain notes.\n", mtime=T0)
    write(d / "README.md", "ignored\n")
    write(d / "notes.txt", "ignored\n")
    lanes = {ln.name: ln for ln in collect_local({"type": "local", "name": "laptop", "path": str(d)})}
    assert set(lanes) == {"frontend-refactor", "flaky-ci"}
    assert lanes["frontend-refactor"].status == "working"
    assert lanes["frontend-refactor"].title == "Frontend"
    assert lanes["flaky-ci"].updated == T0
    assert lanes["flaky-ci"].signals[0].source == "laptop"


def test_local_missing_path_is_an_error_lane(tmp_path):
    (ln,) = collect_local({"name": "x", "path": str(tmp_path / "nope")})
    assert ln.errors and "not found" in ln.errors[0]


def test_local_lane_override_and_single_file(tmp_path):
    f = write(tmp_path / "x.md", "---\nlane: renamed\n---\nhi\n")
    (ln,) = collect_local({"path": str(f)})
    assert ln.name == "renamed"


# ---------------------------------------------------------------- ssh

def test_ssh_reads_glob_with_mtime(fake_ssh):
    cmd, root = fake_ssh
    write(root / "gpu-box" / "work" / "lanes" / "paper-experiments.md",
          "---\nstatus: working\nnext: run 4/6\n---\n# Paper experiments\n\nAblations.\n", mtime=T0)
    write(root / "gpu-box" / "work" / "lanes" / "dataset-cleanup.md", "no front matter\n", mtime=T0)
    lanes = {ln.name: ln for ln in collect_ssh(
        {"type": "ssh", "host": "gpu-box", "paths": ["~/work/lanes/*.md"], "ssh_command": cmd})}
    assert set(lanes) == {"paper-experiments", "dataset-cleanup"}
    pe = lanes["paper-experiments"]
    assert pe.status == "working" and pe.next == "run 4/6" and pe.host == "gpu-box"
    assert pe.updated == T0  # from remote stat, front-matter has no `updated`
    assert lanes["dataset-cleanup"].summary == "no front matter"
    calls = (root / "calls.log").read_text()
    assert "BatchMode=yes" in calls


def test_ssh_path_with_spaces_and_no_mtime(fake_ssh):
    cmd, root = fake_ssh
    write(root / "box" / "my lanes" / "a b.md", "---\nupdated: 2026-05-01T09:00:00Z\n---\nx\n")
    (ln,) = collect_ssh({"host": "box", "path": "~/my lanes/a b.md", "ssh_command": cmd, "remote_mtime": False})
    assert ln.name == "a b" and ln.updated == T0


def test_ssh_paths_are_quoted_not_executed(fake_ssh, tmp_path):
    cmd, root = fake_ssh
    (root / "box").mkdir()
    canary = tmp_path / "pwned"
    (ln,) = collect_ssh({"host": "box", "path": f"~/x$(touch {canary}).md", "ssh_command": cmd})
    assert not canary.exists()
    assert ln.errors


def test_ssh_rejects_unsafe_glob_and_host():
    with pytest.raises(ValueError):
        build_remote_command("~/lanes/*;rm -rf ~.md", "@@m")
    with pytest.raises(ValueError):
        collect_ssh({"host": "-oProxyCommand=evil", "path": "x.md"})


def test_ssh_unreachable_and_auth_errors(fake_ssh):
    cmd, _ = fake_ssh
    (ln,) = collect_ssh({"host": "nohost", "path": "~/a.md", "ssh_command": cmd})
    assert "Could not resolve hostname" in ln.errors[0]
    (ln,) = collect_ssh({"host": "auth-fail", "path": "~/a.md", "ssh_command": cmd})
    assert "Permission denied" in ln.errors[0]


def test_ssh_missing_file(fake_ssh):
    cmd, root = fake_ssh
    (root / "box").mkdir()
    (ln,) = collect_ssh({"host": "box", "path": "~/lanes/none.md", "ssh_command": cmd})
    assert ln.name == "none" and "no matching file" in ln.errors[0]


def test_ssh_timeout(fake_ssh):
    cmd, _ = fake_ssh
    (ln,) = collect_ssh({"host": "slow-host", "path": "~/a.md", "ssh_command": cmd, "timeout": 0.5})
    assert "timed out" in ln.errors[0]


def test_ssh_binary_missing():
    (ln,) = collect_ssh({"host": "box", "path": "~/a.md", "ssh_command": ["/nonexistent/ssh"]})
    assert "not found" in ln.errors[0]


def test_parse_remote_output_handles_content_lookalikes():
    out = "\n@@m 100 /h/a.md\nline\n@@mx 1 fake\n\n@@m 0 /h/b.md\nbody"
    items = parse_remote_output(out, "@@m")
    assert [i[0] for i in items] == ["/h/a.md", "/h/b.md"]
    assert items[0][2] == "line\n@@mx 1 fake\n" and items[1][1] is None


# ---------------------------------------------------------------- git

def _git(repo, *args, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def test_git_repo_info(tmp_path):
    repo = tmp_path / "frontend-refactor"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "feature/forms")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "port profile page", when=T0)
    (repo / "a.txt").write_text("changed")
    (repo / "new.txt").write_text("n")
    lanes = collect([{"type": "git", "paths": [str(repo), {"path": str(repo), "lane": "alias"}]}])
    by = {ln.name: ln for ln in lanes}
    ln = by["frontend-refactor"]
    assert ln.updated == T0
    assert ln.signals[0].detail.startswith("feature/forms, 2 uncommitted")
    assert "port profile page" in ln.signals[0].detail
    assert "alias" in by
    # read-only: no index.lock or other writes left behind
    assert not (repo / ".git" / "index.lock").exists()


def test_git_empty_repo_and_non_repo(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q")
    plain = tmp_path / "plain"
    plain.mkdir()
    lanes = {ln.name: ln for ln in collect([{"type": "git", "paths": [str(empty), str(plain)]}])}
    assert lanes["empty"].updated is None and not lanes["empty"].errors
    assert lanes["plain"].errors and "not a git repo" in lanes["plain"].errors[0]


# ---------------------------------------------------------------- agent logs (experimental)

def _jsonl(path, rows, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n")
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))


def test_claude_code_adapter(tmp_path):
    root = tmp_path / "projects"
    cwd = "/srv/demo/projects/frontend-refactor"
    _jsonl(root / "-srv-demo-projects-frontend-refactor" / "s1.jsonl", [
        {"type": "summary", "summary": "x"},
        {"type": "user", "cwd": cwd, "timestamp": "2026-05-01T08:00:00Z", "message": {"role": "user", "content": "go"}},
        "not json at all",
        {"type": "assistant", "cwd": cwd, "timestamp": "2026-05-01T08:30:00Z",
         "message": {"content": [{"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "Ported   the page."}]}},
        {"type": "user", "cwd": cwd, "timestamp": "2026-05-01T08:31:00Z",
         "message": {"content": [{"type": "tool_result", "content": "big output"}]}},
    ], T0)
    _jsonl(root / "-srv-demo-projects-frontend-refactor" / "old.jsonl", [
        {"type": "user", "cwd": cwd, "timestamp": "2026-04-01T08:00:00Z", "message": {"content": "older"}}],
        T0 - timedelta(days=30))
    (ln,) = collect_sessions(ClaudeCodeAdapter(), {"path": str(root)}, now=T0)
    assert ln.name == "frontend-refactor"
    assert ln.updated == datetime(2026, 5, 1, 8, 31, tzinfo=timezone.utc)
    assert ln.signals[0].detail == "agent: Ported the page."
    assert "secret" not in ln.signals[0].detail


def test_codex_adapter_new_and_legacy_formats(tmp_path):
    root = tmp_path / "sessions"
    _jsonl(root / "2026" / "05" / "01" / "rollout-2026-05-01T08-00-00-abc.jsonl", [
        {"timestamp": "2026-05-01T08:00:00Z", "type": "session_meta", "payload": {"id": "abc", "cwd": "/srv/p/api-rate-limits"}},
        {"timestamp": "2026-05-01T08:01:00Z", "type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "<environment_context><cwd>/elsewhere</cwd></environment_context>"}]}},
        {"timestamp": "2026-05-01T08:20:00Z", "type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "Which strategy should be default?"}]}},
        {"timestamp": "2026-05-01T08:21:00Z", "type": "event_msg", "payload": {"type": "token_count"}},
    ], T0)
    _jsonl(root / "2026" / "04" / "30" / "rollout-legacy.jsonl", [
        {"id": "x", "timestamp": "2026-04-30T10:00:00Z", "instructions": ""},
        {"type": "message", "role": "user", "content": [{"type": "input_text",
         "text": "<environment_context>\n<cwd>/srv/p/paper-experiments</cwd>\n</environment_context>"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Run 3 done."}]},
    ], T0 - timedelta(hours=20))
    lanes = {ln.name: ln for ln in collect_sessions(CodexAdapter(), {"path": str(root)}, now=T0)}
    assert lanes["api-rate-limits"].signals[0].detail == "agent: Which strategy should be default?"
    assert lanes["api-rate-limits"].updated == datetime(2026, 5, 1, 8, 21, tzinfo=timezone.utc)
    assert lanes["paper-experiments"].signals[0].detail == "agent: Run 3 done."


def test_agent_source_missing_dir_is_silent_by_default(tmp_path):
    assert collect([{"type": "codex", "path": str(tmp_path / "none")}]) == []
    (ln,) = collect([{"type": "claude-code", "path": str(tmp_path / "none"), "optional": False}])
    assert ln.errors


def test_aider_adapter(tmp_path):
    repo = tmp_path / "docs-site"
    write(repo / ".aider.chat.history.md", "# aider chat started\n\n#### fix the links\n\nok\n\n#### now redirects\n", mtime=T0)
    (ln,) = collect([{"type": "aider", "paths": [str(repo)]}])
    assert ln.name == "docs-site" and ln.signals[0].detail == "you: now redirects" and ln.updated == T0


# ---------------------------------------------------------------- registry / merge

def test_unknown_type_and_crashing_source_do_not_break_the_board(tmp_path, monkeypatch):
    from laneboard import sources

    def boom(spec):
        raise RuntimeError("kaput")

    sources.get_source("local")
    monkeypatch.setitem(sources._REGISTRY, "boom", boom)
    lanes = collect([{"type": "nope", "name": "n"}, {"type": "boom", "name": "b"}])
    errs = sorted(e for ln in lanes for e in ln.errors)
    assert errs == ["b: RuntimeError: kaput", "n: unknown source type 'nope'"]


def test_sources_merge_into_one_lane(tmp_path, fake_ssh):
    cmd, root = fake_ssh
    write(tmp_path / "lanes" / "paper-experiments.md", "---\nstatus: blocked\n---\nlocal view\n", mtime=T0)
    write(root / "gpu" / "lanes" / "paper-experiments.md", "---\nowner: codex\n---\nremote view\n",
          mtime=T0 + timedelta(hours=1))
    lanes = collect([{"type": "local", "path": str(tmp_path / "lanes"), "stale_hours": 1},
                     {"type": "ssh", "host": "gpu", "path": "~/lanes/*.md", "ssh_command": cmd}])
    (ln,) = lanes
    assert ln.status == "blocked" and ln.owner == "codex" and ln.updated == T0 + timedelta(hours=1)
    assert len(ln.signals) == 2 and ln.stale_hours == 1


def test_fresh_process_parallel_collect_loads_registry(tmp_path):
    """Regression: the registry must be loaded before sources run in parallel threads."""
    import sys
    code = ("from laneboard.sources import collect\n"
            "lanes = collect([{'type': t, 'path': '/nonexistent'} for t in ('git', 'local', 'aider', 'codex')])\n"
            "assert not any('unknown source type' in e for ln in lanes for e in ln.errors), lanes\n")
    subprocess.run([sys.executable, "-c", code], check=True, cwd=tmp_path)
