from __future__ import annotations
import fcntl
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
    # Writes in place (open + truncate + write), NOT tmp-file + os.replace.
    # In prod these .MD files are individual single-file bind mounts shared with
    # the embedding host (renewals: see docker-compose.yml). Renaming a tmp file
    # onto such a mount fails with EXDEV ("Invalid cross-device link"); and on a
    # directory mount a rename swaps the inode, desyncing any other mount/watcher
    # bound to the original file (e.g. the renewals `web` container's own view of
    # BUGS.MD). Writing in place keeps the same inode, matching what
    # renewals/backend/services/bug_report_service.py already had to do.
    #
    # Callers already hold _lock_for(path) (see the parsers), so we must NOT
    # re-acquire it here - threading.Lock is non-reentrant and would deadlock.
    # The exclusive flock below adds cross-*process* serialization, since a
    # separate process (renewals' web workers) writes BUGS.MD directly.
    data = text.encode("utf-8")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        view = memoryview(data)
        written = 0
        while written < len(data):
            written += os.write(fd, view[written:])
        os.fsync(fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    # Best-effort background export to the content-sync branch - never lets a
    # sync problem surface as a failed save (see git_sync.schedule_sync).
    from . import git_sync
    git_sync.schedule_sync("save")
