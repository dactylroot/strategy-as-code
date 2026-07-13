from __future__ import annotations
import os
import threading
from pathlib import Path

_locks: dict[Path, threading.Lock] = {}
_locks_mutex = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _locks_mutex:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    # Best-effort background export to the content-sync branch - never lets a
    # sync problem surface as a failed save (see git_sync.schedule_sync).
    from . import git_sync
    git_sync.schedule_sync("save")
