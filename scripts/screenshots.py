#!/usr/bin/env python3
"""Take README screenshots of a running board (dev helper, needs `pip install playwright`).

    python scripts/screenshots.py http://127.0.0.1:8765/ docs/screenshots

Uses an already-installed Chromium (PLAYWRIGHT_BROWSERS_PATH or CHROMIUM_PATH);
it never downloads browsers.
"""

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
out = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/screenshots")
out.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    kw = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
    browser = p.chromium.launch(**kw)
    for name, opts in {
        "desktop": {"viewport": {"width": 1280, "height": 860}, "device_scale_factor": 1},
        "mobile": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True,
                   "has_touch": True},
    }.items():
        for scheme in ("light", "dark"):
            ctx = browser.new_context(color_scheme=scheme, **opts)
            page = ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(url, wait_until="networkidle")
            page.wait_for_selector(".lane")
            suffix = "" if scheme == "light" else "-dark"
            path = out / f"{name}{suffix}.png"
            page.screenshot(path=str(path), full_page=(name == "desktop"))
            if errors:
                print("console errors:", errors, file=sys.stderr)
            print(path)
            ctx.close()
    browser.close()
