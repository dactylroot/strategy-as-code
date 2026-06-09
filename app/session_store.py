from __future__ import annotations
import threading
import time
import uuid
from dataclasses import dataclass, field

SESSION_TTL = 2 * 60 * 60  # 2 hours

_FILENAMES = ("PRODUCT.MD", "ABOUT.MD", "BUGS.MD", "README.MD")


@dataclass
class Session:
    session_id: str
    files: dict[str, str]
    title: str
    last_active: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_active = time.monotonic()

    def get_file(self, name: str) -> str:
        return self.files.get(name.upper(), "")

    def set_file(self, name: str, content: str) -> None:
        self.files[name.upper()] = content


_store: dict[str, Session] = {}
_lock = threading.Lock()


def create(files: dict[str, str], title: str = "") -> str:
    sid = str(uuid.uuid4())
    normalized = {k.upper(): v for k, v in files.items()}
    with _lock:
        _store[sid] = Session(session_id=sid, files=normalized, title=title)
    return sid


def get(sid: str | None) -> Session | None:
    if not sid:
        return None
    with _lock:
        s = _store.get(sid)
        if s is None:
            return None
        if time.monotonic() - s.last_active > SESSION_TTL:
            del _store[sid]
            return None
        s.touch()
        return s


def update_file(sid: str, filename: str, content: str) -> None:
    with _lock:
        s = _store.get(sid)
        if s:
            s.set_file(filename, content)
            s.touch()


def cleanup_expired() -> int:
    now = time.monotonic()
    with _lock:
        expired = [sid for sid, s in _store.items() if now - s.last_active > SESSION_TTL]
        for sid in expired:
            del _store[sid]
        return len(expired)
