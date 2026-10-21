import json
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import NOW, write
from laneboard.cli import main
from laneboard.config import ConfigError, load_config, parse_config_dict
from laneboard.model import Lane
from laneboard.notify import load_state, run_notifications, transitions
from laneboard.render import render_page, render_table
from laneboard.rules import evaluate


def make_cfg(tmp_path, extra=""):
    lanes = tmp_path / "lanes"
    write(lanes / "frontend-refactor.md", f"---\nstatus: working\nupdated: {NOW.isoformat()}\n---\n# Frontend refactor\n\nok\n")
    write(lanes / "api-rate-limits.md", "---\nstatus: blocked\nnext: pick a strategy\n---\nneeds decision\n")
    write(lanes / "xss.md", "---\nowner: <script>alert(1)</script>\n---\n# <img src=x onerror=alert(1)>\n")
    cfg = tmp_path / "laneboard.toml"
    cfg.write_text(f'[board]\ntitle = "Test board"\nstate_file = "{tmp_path / "state.json"}"\n'
                   f'[[source]]\ntype = "local"\npath = "{lanes}"\n' + extra)
    return cfg


# ---------------------------------------------------------------- config

def test_config_validation(tmp_path):
    with pytest.raises(ConfigError):
        parse_config_dict({"source": [{"path": "x"}]})
    with pytest.raises(ConfigError):
        parse_config_dict({"notify": [{"type": "email", "url": "x"}]})
    cfg = parse_config_dict({"board": {"stale_hours": 2}, "sources": {"type": "local", "path": "x"}})
    assert cfg.stale_hours == 2 and len(cfg.sources) == 1 and cfg.refresh_seconds == 30
    bad = tmp_path / "bad.toml"
    bad.write_text("[board\n")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_yaml_config(tmp_path):
    pytest.importorskip("yaml")
    p = tmp_path / "c.yaml"
    p.write_text("board:\n  stale_hours: 3\nsources:\n  - type: local\n    path: ~/projects/lanes\n")
    cfg = load_config(p)
    assert cfg.stale_hours == 3 and cfg.sources[0]["type"] == "local"


# ---------------------------------------------------------------- rendering

def test_html_is_escaped_and_self_contained(tmp_path):
    cfg = load_config(make_cfg(tmp_path))
    from laneboard.board import build
    lanes = build(cfg, now=NOW)
    page = render_page(lanes, title=cfg.title, now=NOW, stale_hours=6)
    assert "<script>alert(1)</script>" not in page and "<img src=x" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "http://" not in page and "https://" not in page  # no external assets
    assert 'data-state="blocked"' in page and "Blocked on:" in page


def test_terminal_table():
    lanes = evaluate([Lane(name="a", status="blocked", updated=NOW - timedelta(hours=1), next="decide"),
                      Lane(name="b", status="working", updated=NOW - timedelta(hours=9))], stale_hours=6, now=NOW)
    out = render_table(lanes, now=NOW, color=False, width=100)
    lines = out.splitlines()
    assert lines[1].startswith("blocked") and "next: decide" in lines[1]
    assert lines[2].startswith("stale")
    assert "1 blocked, 1 stale" in out
    assert "\033[" in render_table(lanes, now=NOW, color=True)


# ---------------------------------------------------------------- CLI

def test_cli_snapshot_status_init(tmp_path, capsys):
    cfg = make_cfg(tmp_path)
    out = tmp_path / "out.html"
    js = tmp_path / "out.json"
    assert main(["-c", str(cfg), "snapshot", "--html", str(out), "--json", str(js)]) == 0
    html = out.read_text()
    assert html.startswith("<!doctype html>") and "Frontend refactor" in html and "snapshot taken" in html
    data = json.loads(js.read_text())
    assert {ln["name"] for ln in data["lanes"]} == {"frontend-refactor", "api-rate-limits", "xss"}
    assert main(["-c", str(cfg), "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["lanes"][0]["state"] == "blocked"
    assert main(["-c", str(cfg), "status", "--no-color"]) == 0
    assert "api-rate-limits" in capsys.readouterr().out.replace("API rate limits", "api-rate-limits")
    init = tmp_path / "new" / "laneboard.toml"
    assert main(["init", str(init)]) == 0
    assert load_config(init).sources[0]["type"] == "local"
    assert main(["init", str(init)]) == 1
    assert main(["-c", str(tmp_path / "missing.toml"), "status"]) == 2


# ---------------------------------------------------------------- serve

def test_serve_endpoints(tmp_path):
    from laneboard.server import is_loopback, make_server
    cfg = load_config(make_cfg(tmp_path))
    srv = make_server(cfg, "127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as r:
            body = r.read().decode()
            csp = r.headers["Content-Security-Policy"]
        nonce = csp.split("'nonce-")[1].split("'")[0]
        assert f'<script nonce="{nonce}">' in body and "live, refreshes every 30s" in body
        assert "default-src 'none'" in csp
        with urllib.request.urlopen(base + "/?fragment=1") as r:
            frag = r.read().decode()
        assert frag.startswith('<div id="asof"') and "<html" not in frag
        with urllib.request.urlopen(base + "/api/lanes") as r:
            assert len(json.loads(r.read())["lanes"]) == 3
        req = urllib.request.Request(base + "/", data=b"x", method="POST")
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req)
        assert ei.value.code == 405
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(base + "/../etc/passwd")
        assert ei.value.code == 404
    finally:
        srv.shutdown()
        srv.server_close()
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("example.invalid")


# ---------------------------------------------------------------- notifications

class Sink:
    def __init__(self, fail=False):
        self.requests = []
        sink = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                sink.requests.append((self.path, dict(self.headers), self.rfile.read(n)))
                self.send_response(500 if fail else 200)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def test_transitions_only_on_change():
    a = Lane(name="a", status="blocked")
    b = Lane(name="b", status="working")
    assert [(ln.name, o, n) for ln, o, n in transitions([a, b], {})] == [("a", "", "blocked")]
    assert transitions([a, b], {"a": "blocked"}) == []


def test_ntfy_and_webhook_delivery(tmp_path, monkeypatch):
    sink = Sink()
    state = tmp_path / "state" / "s.json"
    monkeypatch.setenv("LB_TEST_TOKEN", "t0ken")
    targets = [{"type": "ntfy", "url": sink.url + "/topic"},
               {"type": "webhook", "url_env": "LB_TEST_URL", "headers_env": {"Authorization": "LB_TEST_TOKEN"},
                "on": ["blocked"]}]
    monkeypatch.setenv("LB_TEST_URL", sink.url + "/hook")
    try:
        lanes = evaluate([Lane(name="api", title="API limits", status="blocked", updated=NOW, host="laptop",
                               next="pick one"),
                          Lane(name="old", status="working", updated=NOW - timedelta(days=1))],
                         stale_hours=6, now=NOW)
        log = run_notifications(lanes, targets, state)
        assert sorted(log) == ["sent ntfy: api -> blocked", "sent ntfy: old -> stale", "sent webhook: api -> blocked"]
        ntfy = [r for r in sink.requests if r[0] == "/topic"]
        assert ntfy[0][1]["Title"] == "[blocked] API limits" and ntfy[0][2] == "on laptop — next: pick one".encode()
        hook = [r for r in sink.requests if r[0] == "/hook"][0]
        assert hook[1]["Authorization"] == "t0ken"
        payload = json.loads(hook[2])
        assert payload["event"] == "lane.blocked" and payload["data"]["next"] == "pick one"
        assert load_state(state) == {"api": "blocked", "old": "stale"}
        # second run: nothing new
        assert run_notifications(lanes, targets, state) == []
    finally:
        sink.close()


def test_failed_push_is_retried(tmp_path):
    sink = Sink(fail=True)
    state = tmp_path / "s.json"
    lanes = [Lane(name="api", status="blocked")]
    try:
        log = run_notifications(lanes, [{"type": "ntfy", "url": sink.url}], state)
        assert log[0].startswith("failed ntfy")
        assert load_state(state) == {"api": ""}
        assert len(transitions(lanes, load_state(state))) == 1
    finally:
        sink.close()
