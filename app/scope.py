"""Per-request project scope for program mode.

A request to `<base>/p/<key>/...` is served by the ordinary pages and API with
the project directory, title and URL prefix switched to member project `<key>`.
The switch is a ContextVar set by the middleware in `main.py`, so nothing in the
routers or parsers has to know about it. Kept free of app settings so `config`
can import it.
"""
from __future__ import annotations
import time
from contextvars import ContextVar
from pathlib import Path

from .registry import Registry

current_project: ContextVar[str | None] = ContextVar("current_project", default=None)

# True only while an endpoint body runs inside writer.edit; see program_write.
writing: ContextVar[bool] = ContextVar("scope_writing", default=False)

_registry: Registry | None = None


def set_registry(registry: Registry | None) -> None:
    global _registry
    _registry = registry


def get_registry() -> Registry | None:
    return _registry


def scope_key() -> str | None:
    return current_project.get()


def scoped_dir() -> Path | None:
    """Directory holding the scoped project's planning files, or None when unscoped."""
    key = current_project.get()
    if key is None or _registry is None or key not in _registry:
        return None
    return _registry.get(key).planning_root


def freshen(key: str, max_age: float = 30.0) -> bool:
    """Make sure `key`'s clone exists and is at most `max_age` seconds old.

    Returns True when the data being served may be stale (the refresh failed but
    an older clone is available). Raises when there is no usable clone at all.
    Blocking: call from a worker thread.
    """
    assert _registry is not None
    project = _registry.get(key)
    last = project.status.last_fetch
    if (project.path / ".git").exists() and last is not None and time.time() - last < max_age:
        return False
    try:
        _registry.refresh(key)
        return False
    except Exception:
        if (project.path / ".git").exists() and project.status.sha:
            return True
        raise


def branch_label() -> str:
    """Where the scoped project's edits are committed, for display."""
    key = current_project.get()
    if key is None or _registry is None or key not in _registry:
        return ""
    return _registry.get(key).target_branch or "its default branch"
