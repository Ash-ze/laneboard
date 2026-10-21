"""Glue: config -> sources -> rules."""

from __future__ import annotations

import threading
import time
from datetime import datetime

from .config import Config
from .model import Lane, utcnow
from .rules import evaluate
from .sources import collect


def build(cfg: Config, *, now: datetime | None = None) -> list[Lane]:
    lanes = collect(cfg.sources)
    return evaluate(lanes, stale_hours=cfg.stale_hours, now=now or utcnow())


class CachedBoard:
    """Collects at most once per ``ttl`` seconds, so many open tabs cost one scan."""

    def __init__(self, cfg: Config, ttl: float):
        self.cfg = cfg
        self.ttl = ttl
        self._lock = threading.Lock()
        self._at = 0.0
        self._lanes: list[Lane] = []
        self._now: datetime = utcnow()

    def get(self) -> tuple[list[Lane], datetime]:
        with self._lock:
            if time.monotonic() - self._at >= self.ttl or not self._at:
                self._now = utcnow()
                self._lanes = build(self.cfg, now=self._now)
                self._at = time.monotonic()
            return self._lanes, self._now
