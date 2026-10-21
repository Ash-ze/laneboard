"""HTML and terminal rendering. No external assets: everything is inlined."""

from __future__ import annotations

import html
import json
import os
import shutil
from datetime import datetime

from . import __version__
from .model import Lane, utcnow
from .rules import counts, humanize_age

e = html.escape

CSS = """
:root{--bg:#f4f5f7;--card:#fff;--fg:#1d2330;--muted:#5d6675;--line:#e3e6eb;
--blocked:#d4351c;--blocked-bg:#fdecea;--stale:#b26a00;--stale-bg:#fff4e0;--working:#1a7f4b;
--working-bg:#e6f5ec;--done:#6b7280;--done-bg:#eef0f3;--unknown:#4b5bd6;--unknown-bg:#eceefe;
--error:#9b1c5a;--error-bg:#fbe9f1;--chip:#eef0f3}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#181c24;--fg:#e6e9ef;--muted:#9aa3b2;
--line:#272c37;--blocked:#ff6b57;--blocked-bg:#3a1915;--stale:#f0a832;--stale-bg:#33270f;
--working:#4cc38a;--working-bg:#132a1f;--done:#8b93a1;--done-bg:#1f232b;--unknown:#8e9bff;
--unknown-bg:#1d2140;--error:#f472b6;--error-bg:#3a1428;--chip:#222733}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px 10px}
.top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;max-width:1200px;margin:0 auto}
h1{font-size:18px;margin:0;font-weight:650;letter-spacing:-.01em}
.asof{color:var(--muted);font-size:13px;margin-left:auto}
.chips{display:flex;gap:6px;flex-wrap:wrap;max-width:1200px;margin:8px auto 0}
.chip{border:0;border-radius:999px;padding:4px 11px;font:inherit;font-size:13px;background:var(--chip);color:var(--fg);cursor:pointer}
.chip b{font-variant-numeric:tabular-nums}
.chip.blocked{background:var(--blocked-bg);color:var(--blocked)}
.chip.stale{background:var(--stale-bg);color:var(--stale)}
.chip.working{background:var(--working-bg);color:var(--working)}
.chip.done{background:var(--done-bg);color:var(--done)}
.chip[aria-pressed=true]{outline:2px solid currentColor;outline-offset:-2px}
main{max-width:1200px;margin:0 auto;padding:14px 16px 40px;display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(340px,1fr))}
.lane{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--unknown);border-radius:10px;padding:12px 14px;min-width:0}
.lane.blocked{border-left-color:var(--blocked);box-shadow:0 0 0 1px var(--blocked) inset,0 1px 6px rgba(212,53,28,.15)}
.lane.stale{border-left-color:var(--stale)}
.lane.working{border-left-color:var(--working)}
.lane.done{border-left-color:var(--done);opacity:.72}
.lane.error{border-left-color:var(--error)}
.row{display:flex;align-items:center;gap:8px}
.name{font-weight:620;font-size:16px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}
.badge{font-size:12px;font-weight:650;text-transform:uppercase;letter-spacing:.04em;padding:2px 8px;border-radius:6px;white-space:nowrap}
.badge.blocked{background:var(--blocked-bg);color:var(--blocked)}
.badge.stale{background:var(--stale-bg);color:var(--stale)}
.badge.working{background:var(--working-bg);color:var(--working)}
.badge.done{background:var(--done-bg);color:var(--done)}
.badge.unknown,.badge.other{background:var(--unknown-bg);color:var(--unknown)}
.badge.error{background:var(--error-bg);color:var(--error)}
.meta{color:var(--muted);font-size:13px;margin-top:2px;display:flex;gap:4px 12px;flex-wrap:wrap}
.age{font-variant-numeric:tabular-nums;white-space:nowrap}
.age.old{color:var(--stale);font-weight:600}
.summary{margin:8px 0 0;overflow-wrap:anywhere}
.next{margin:8px 0 0;padding:6px 9px;border-radius:7px;background:var(--chip);font-size:14px;overflow-wrap:anywhere}
.lane.blocked .next{background:var(--blocked-bg)}
.next b{font-weight:650}
.sig{list-style:none;margin:10px 0 0;padding:8px 0 0;border-top:1px dashed var(--line);font-size:13px;color:var(--muted)}
.sig li{display:flex;gap:8px;padding:2px 0;min-width:0}
.sig .k{flex:none;width:82px;overflow:hidden;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--fg);opacity:.8}
.sig .d{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sig .t{flex:none;font-variant-numeric:tabular-nums}
.sig .err{color:var(--error)}
.empty{grid-column:1/-1;text-align:center;color:var(--muted);padding:40px 0}
footer{color:var(--muted);font-size:12px;text-align:center;padding:0 16px 24px}
.hide{display:none!important}
@media (max-width:520px){main{grid-template-columns:1fr;padding:12px 12px 32px;gap:10px}header{padding:10px 12px 8px}
.asof{width:100%;margin-left:0;order:3}.sig .d{white-space:normal;overflow-wrap:anywhere}}
"""

JS = """
(function(){
 var REFRESH=%(refresh)d, live=%(live)s, filter=null;
 function apply(){document.querySelectorAll('.lane').forEach(function(el){
   var s=el.getAttribute('data-state');
   el.classList.toggle('hide', !!filter && (filter==='attention' ? !(s==='blocked'||s==='stale'||s==='error') : s!==filter));});
  document.querySelectorAll('.chip[data-f]').forEach(function(c){c.setAttribute('aria-pressed', String(c.getAttribute('data-f')===filter));});}
 document.addEventListener('click',function(ev){var c=ev.target.closest('.chip[data-f]');if(!c)return;
   var f=c.getAttribute('data-f');filter=(filter===f?null:f);apply();});
 function refresh(){if(!live)return;fetch('?fragment=1',{cache:'no-store'}).then(function(r){
   if(!r.ok)throw new Error(r.status);return r.text();}).then(function(t){
   var d=document.createElement('div');d.innerHTML=t;
   ['#chips','#board','#asof'].forEach(function(sel){var n=d.querySelector(sel),o=document.querySelector(sel);if(n&&o)o.replaceWith(n);});
   apply();}).catch(function(){var a=document.querySelector('#asof');if(a)a.textContent='offline \\u2014 retrying';});}
 if(live){setInterval(function(){if(!document.hidden)refresh();},REFRESH*1000);
   document.addEventListener('visibilitychange',function(){if(!document.hidden)refresh();});}
})();
"""


def _local_time(ts: datetime) -> str:
    return ts.astimezone().strftime("%Y-%m-%d %H:%M %Z").strip()


def render_lane(lane: Lane, now: datetime, stale_hours: float) -> str:
    st = lane.state
    badge_cls = st if st in ("blocked", "stale", "working", "done", "unknown", "error") else "other"
    hours = lane.stale_hours if lane.stale_hours is not None else stale_hours
    age = humanize_age(lane.updated, now)
    old = " old" if lane.stale else ""
    title_attr = f' title="{e(_local_time(lane.updated))}"' if lane.updated else ""
    meta = [f'<span class="age{old}"{title_attr}>updated {e(age)}</span>']
    if lane.host:
        meta.append(f"<span>@ {e(lane.host)}</span>")
    if lane.owner:
        meta.append(f"<span>{e(lane.owner)}</span>")
    if lane.stale:
        meta.append(f"<span>stale &gt; {hours:g}h</span>")
    parts = [f'<article class="lane {badge_cls}" data-state="{e(st)}" data-name="{e(lane.name)}">',
             f'<div class="row"><div class="name" title="{e(lane.name)}">{e(lane.display_title)}</div>'
             f'<span class="badge {badge_cls}">{e(st)}</span></div>',
             f'<div class="meta">{"".join(meta)}</div>']
    if lane.summary:
        parts.append(f'<p class="summary">{e(lane.summary)}</p>')
    if lane.next:
        label = "Blocked on" if st == "blocked" else "Next"
        parts.append(f'<p class="next"><b>{label}:</b> {e(lane.next)}</p>')
    if lane.signals:
        items = []
        for s in lane.signals:
            if s.error:
                d = f'<span class="d err" title="{e(s.location)}">{e(s.error)}</span>'
            else:
                d = f'<span class="d" title="{e(s.detail)} — {e(s.location)}">{e(s.detail)}</span>'
            t = f'<span class="t">{e(humanize_age(s.updated, now))}</span>' if s.updated else ""
            items.append(f'<li><span class="k">{e(s.kind)}</span>{d}{t}</li>')
        parts.append(f'<ul class="sig">{"".join(items)}</ul>')
    parts.append("</article>")
    return "".join(parts)


def render_parts(lanes: list[Lane], *, now: datetime, stale_hours: float) -> tuple[str, str, str]:
    c = counts(lanes)
    chips = [f'<button class="chip" data-f="attention">needs attention <b>{c["blocked"] + c["stale"] + c["error"]}</b></button>']
    for k in ("blocked", "stale", "working", "done"):
        chips.append(f'<button class="chip {k}" data-f="{k}">{k} <b>{c[k]}</b></button>')
    chips.append(f'<span class="chip">total <b>{len(lanes)}</b></span>')
    body = "".join(render_lane(lane, now, stale_hours) for lane in lanes) or \
        '<div class="empty">No lanes yet. Add sources to your config.</div>'
    asof = (f'<div id="asof" class="asof" title="{e(_local_time(now))}">'
            f'as of {e(now.astimezone().strftime("%H:%M:%S"))}</div>')
    return asof, f'<div id="chips" class="chips">{"".join(chips)}</div>', f'<main id="board">{body}</main>'


def render_fragment(lanes: list[Lane], *, now: datetime, stale_hours: float) -> str:
    return "".join(render_parts(lanes, now=now, stale_hours=stale_hours))


def render_page(lanes: list[Lane], *, title: str, now: datetime | None = None, stale_hours: float,
                live: bool = False, refresh_seconds: int = 30, nonce: str | None = None) -> str:
    now = now or utcnow()
    asof, chips, board = render_parts(lanes, now=now, stale_hours=stale_hours)
    n = f' nonce="{e(nonce)}"' if nonce else ""
    js = JS % {"refresh": refresh_seconds, "live": "true" if live else "false"}
    mode = f"live, refreshes every {refresh_seconds}s" if live else f"snapshot taken {e(_local_time(now))}"
    c = counts(lanes)
    attn = c["blocked"] + c["stale"] + c["error"]
    doc_title = f"({attn}) {title}" if attn else title
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="robots" content="noindex,nofollow">
<title>{e(doc_title)}</title>
<link rel="icon" href="data:,">
<style{n}>{CSS}</style></head>
<body>
<header><div class="top"><h1>{e(title)}</h1>{asof}</div>{chips}</header>
{board}
<footer>laneboard {e(__version__)} &middot; read-only &middot; {mode}</footer>
<script{n}>{js}</script>
</body></html>
"""


# ---------------------------------------------------------------- terminal

ANSI = {"blocked": "\033[1;31m", "stale": "\033[33m", "working": "\033[32m", "done": "\033[2m",
        "error": "\033[35m", "unknown": "\033[34m"}
RESET = "\033[0m"


def _trunc(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def render_table(lanes: list[Lane], *, now: datetime | None = None, color: bool | None = None,
                 width: int | None = None) -> str:
    now = now or utcnow()
    if color is None:
        color = os.isatty(1) and not os.environ.get("NO_COLOR")
    width = width or shutil.get_terminal_size((110, 24)).columns
    cols = [("STATE", 8), ("LANE", 24), ("UPDATED", 10), ("HOST", 12)]
    fixed = sum(w + 2 for _, w in cols)
    rest = max(20, width - fixed)
    lines = ["  ".join(h.ljust(w) for h, w in cols) + "  NEXT / SUMMARY"]
    for lane in lanes:
        st = lane.state
        info = lane.next and f"next: {lane.next}" or lane.summary or "; ".join(lane.errors) or \
            next((s.detail for s in lane.signals if s.detail), "")
        cells = [st, _trunc(lane.display_title, 24), humanize_age(lane.updated, now).replace(" ago", ""),
                 _trunc(lane.host, 12)]
        row = "  ".join(c.ljust(w) for c, (_, w) in zip(cells, cols)) + "  " + _trunc(info, rest)
        if color and st in ANSI:
            row = ANSI[st] + row + RESET
        lines.append(row)
    c = counts(lanes)
    lines.append("")
    lines.append(f"{len(lanes)} lanes: {c['blocked']} blocked, {c['stale']} stale, {c['working']} working, "
                 f"{c['done']} done  ({now.astimezone().strftime('%H:%M:%S')})")
    return "\n".join(lines)


def lanes_json(lanes: list[Lane], now: datetime) -> str:
    return json.dumps({"generated": now.isoformat(), "lanes": [lane.to_dict() for lane in lanes]}, indent=1)
