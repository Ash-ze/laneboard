"""Data sources. Each source turns one config block into a list of lanes.

A source is a callable ``(spec: dict) -> list[Lane]``. Sources must be
read-only: they may read files, run ``git`` read commands or run the
user's own ``ssh`` binary, and nothing else.

Third-party sources/adapters can register via the ``laneboard.sources``
entry-point group (``name = "package.module:function"``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import entry_points

from ..model import Lane, Signal, merge_lanes

SourceFn = Callable[[dict], list[Lane]]

_REGISTRY: dict[str, SourceFn] = {}
_LOADED = False
_LOAD_LOCK = threading.Lock()


def register(kind: str):
    def deco(fn: SourceFn) -> SourceFn:
        _REGISTRY[kind] = fn
        return fn

    return deco


def _load_builtin() -> None:
    from . import agents, git, local, ssh  # noqa: F401  (registers on import)


def _load_plugins() -> None:
    try:
        eps = entry_points(group="laneboard.sources")
    except Exception:  # pragma: no cover - very old importlib
        return
    for ep in eps:
        if ep.name not in _REGISTRY:
            try:
                _REGISTRY[ep.name] = ep.load()
            except Exception:
                pass


def get_source(kind: str) -> SourceFn | None:
    global _LOADED
    if not _LOADED:
        with _LOAD_LOCK:  # sources run in parallel threads; load the registry exactly once
            if not _LOADED:
                _load_builtin()
                _load_plugins()
                _LOADED = True
    return _REGISTRY.get(kind)


def available() -> list[str]:
    get_source("local")
    return sorted(_REGISTRY)


def _run_one(spec: dict) -> list[Lane]:
    kind = spec.get("type", "local")
    name = spec.get("name") or kind
    fn = get_source(kind)
    if fn is None:
        return [Lane(name=f"{name}", status="", signals=[Signal(source=name, kind=kind, error=f"unknown source type '{kind}'")])]
    try:
        lanes = fn(spec)
    except Exception as e:  # a broken source must never take the board down
        return [Lane(name=f"{name}", signals=[Signal(source=name, kind=kind, error=f"{type(e).__name__}: {e}")])]
    if spec.get("stale_hours") is not None:
        for lane in lanes:
            lane.stale_hours = float(spec["stale_hours"])
    return lanes


def collect(specs: list[dict], *, max_workers: int = 8) -> list[Lane]:
    if not specs:
        return []
    get_source("local")  # make sure the registry is loaded before fanning out
    with ThreadPoolExecutor(max_workers=min(max_workers, len(specs))) as ex:
        results = list(ex.map(_run_one, specs))
    lanes = [lane for group in results for lane in group]
    return merge_lanes(lanes)
