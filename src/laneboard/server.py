"""``laneboard serve``: a tiny read-only HTTP server (stdlib only)."""

from __future__ import annotations

import ipaddress
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .board import CachedBoard
from .config import Config
from .notify import run_notifications
from .render import lanes_json, render_fragment, render_page


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_handler(board: CachedBoard, cfg: Config):
    class Handler(BaseHTTPRequestHandler):
        server_version = "laneboard"
        sys_version = ""

        def log_message(self, fmt, *args):  # quiet by default
            if getattr(self.server, "verbose", False):
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, code: int, body: str, ctype: str, nonce: str | None = None):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            src = f"'nonce-{nonce}'" if nonce else "'none'"
            self.send_header("Content-Security-Policy",
                             f"default-src 'none'; style-src {src}; script-src {src}; connect-src 'self'; "
                             "img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlsplit(self.path)
            q = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                lanes, now = board.get()
                if q.get("fragment"):
                    return self._send(200, render_fragment(lanes, now=now, stale_hours=cfg.stale_hours),
                                      "text/html; charset=utf-8")
                nonce = secrets.token_urlsafe(16)
                page = render_page(lanes, title=cfg.title, now=now, stale_hours=cfg.stale_hours, live=True,
                                   refresh_seconds=cfg.refresh_seconds, nonce=nonce)
                return self._send(200, page, "text/html; charset=utf-8", nonce)
            if url.path == "/api/lanes":
                lanes, now = board.get()
                return self._send(200, lanes_json(lanes, now), "application/json")
            if url.path == "/healthz":
                return self._send(200, "ok\n", "text/plain")
            return self._send(404, "not found\n", "text/plain")

        def do_POST(self):
            self._send(405, "laneboard is read-only\n", "text/plain")

        do_PUT = do_DELETE = do_PATCH = do_POST

    return Handler


def make_server(cfg: Config, host: str, port: int) -> ThreadingHTTPServer:
    board = CachedBoard(cfg, ttl=max(2, cfg.refresh_seconds // 2))
    srv = ThreadingHTTPServer((host, port), make_handler(board, cfg))
    srv.daemon_threads = True
    srv.board = board  # type: ignore[attr-defined]
    return srv


def notify_loop(srv, cfg: Config, stop: threading.Event, interval: float) -> None:
    while not stop.is_set():
        try:
            lanes, _ = srv.board.get()
            for line in run_notifications(lanes, cfg.notify, cfg.state_file):
                print(f"[notify] {line}", file=sys.stderr)
        except Exception as e:  # keep serving even if notifications break
            print(f"[notify] error: {e}", file=sys.stderr)
        stop.wait(interval)


def serve(cfg: Config, host: str, port: int, *, notify: bool = True, verbose: bool = False) -> None:
    if not is_loopback(host):
        print(f"WARNING: binding to {host}. laneboard has no authentication; anyone who can reach "
              "this port can read your lane summaries. Prefer an SSH tunnel or a private VPN.", file=sys.stderr)
    srv = make_server(cfg, host, port)
    srv.verbose = verbose  # type: ignore[attr-defined]
    stop = threading.Event()
    if notify and cfg.notify:
        threading.Thread(target=notify_loop, args=(srv, cfg, stop, max(60, cfg.refresh_seconds)),
                         daemon=True).start()
    h, p = srv.server_address[:2]
    print(f"laneboard serving on http://{h}:{p}/  (Ctrl+C to stop)", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.server_close()
