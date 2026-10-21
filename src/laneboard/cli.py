"""Command line interface."""

from __future__ import annotations

import argparse
import sys
import time
from importlib import resources
from pathlib import Path

from . import __version__
from .board import build
from .config import Config, ConfigError, find_config, load_config
from .model import utcnow
from .notify import run_notifications
from .render import lanes_json, render_page, render_table


def _load(args) -> Config:
    path = find_config(args.config)
    if path is None:
        raise ConfigError("no config found. Create one with `laneboard init` or pass --config FILE "
                          "(searched ./laneboard.toml and ~/.config/laneboard/config.toml)")
    return load_config(path)


def cmd_status(args) -> int:
    cfg = _load(args)
    interval = args.watch
    while True:
        now = utcnow()
        lanes = build(cfg, now=now)
        if args.json:
            out = lanes_json(lanes, now)
        else:
            out = render_table(lanes, now=now, color=None if not args.no_color else False)
        if interval:
            sys.stdout.write("\033[H\033[2J" + f"{cfg.title} — every {interval}s, Ctrl+C to quit\n\n")
        sys.stdout.write(out + "\n")
        sys.stdout.flush()
        if not interval:
            return 0
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            return 0


def cmd_tui(args) -> int:
    if not args.watch:
        args.watch = _load(args).refresh_seconds
    return cmd_status(args)


def cmd_snapshot(args) -> int:
    cfg = _load(args)
    now = utcnow()
    lanes = build(cfg, now=now)
    if not args.html and not args.json:
        print("nothing to do: pass --html FILE and/or --json FILE", file=sys.stderr)
        return 2
    if args.html:
        Path(args.html).write_text(render_page(lanes, title=cfg.title, now=now, stale_hours=cfg.stale_hours),
                                   encoding="utf-8")
        print(f"wrote {args.html} ({len(lanes)} lanes)", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(lanes_json(lanes, now), encoding="utf-8")
        print(f"wrote {args.json}", file=sys.stderr)
    if args.notify and cfg.notify:
        for line in run_notifications(lanes, cfg.notify, cfg.state_file):
            print(f"[notify] {line}", file=sys.stderr)
    return 0


def cmd_notify(args) -> int:
    cfg = _load(args)
    if not cfg.notify:
        print("no [[notify]] targets configured", file=sys.stderr)
        return 2
    lanes = build(cfg)
    for line in run_notifications(lanes, cfg.notify, cfg.state_file):
        print(line)
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    cfg = _load(args)
    host = args.host or cfg.board.get("host", "127.0.0.1")
    port = args.port if args.port is not None else int(cfg.board.get("port", 8765))
    serve(cfg, host, port, notify=not args.no_notify, verbose=args.verbose)
    return 0


def cmd_init(args) -> int:
    target = Path(args.path).expanduser()
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    text = resources.files("laneboard").joinpath("example_config.toml").read_text(encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    return 0


def cmd_sources(args) -> int:
    from .sources import available

    for name in available():
        print(name)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="laneboard", description="Read-only status board for AI coding agents.")
    p.add_argument("--version", action="version", version=f"laneboard {__version__}")
    p.add_argument("-c", "--config", help="config file (default: ./laneboard.toml, ~/.config/laneboard/config.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="print a terminal table")
    s.add_argument("--json", action="store_true", help="print JSON instead of a table")
    s.add_argument("--watch", type=int, metavar="SECONDS", help="redraw every N seconds")
    s.add_argument("--no-color", action="store_true")
    s.set_defaults(fn=cmd_status)

    t = sub.add_parser("tui", help="live terminal view (status --watch)")
    t.add_argument("--watch", type=int, metavar="SECONDS")
    t.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    t.add_argument("--no-color", action="store_true")
    t.set_defaults(fn=cmd_tui)

    sn = sub.add_parser("snapshot", help="write a single self-contained HTML (and/or JSON) file")
    sn.add_argument("--html", metavar="FILE")
    sn.add_argument("--json", metavar="FILE")
    sn.add_argument("--notify", action="store_true", help="also send pending notifications")
    sn.set_defaults(fn=cmd_snapshot)

    sv = sub.add_parser("serve", help="serve the live web board (default 127.0.0.1:8765)")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    sv.add_argument("--no-notify", action="store_true", help="do not send push notifications")
    sv.add_argument("-v", "--verbose", action="store_true", help="log requests")
    sv.set_defaults(fn=cmd_serve)

    n = sub.add_parser("notify", help="check once and push alerts for new blocked/stale lanes (for cron)")
    n.set_defaults(fn=cmd_notify)

    i = sub.add_parser("init", help="write an example config")
    i.add_argument("path", nargs="?", default="laneboard.toml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    so = sub.add_parser("sources", help="list available source types")
    so.set_defaults(fn=cmd_sources)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as e:
        print(f"laneboard: {e}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # pragma: no cover
        return 0
