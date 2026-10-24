"""Keep member-project clones fresh without waiting for a page request.

`poll_loop` refreshes every project each interval; `handle_push_event` lets a
GitHub push webhook trigger an immediate refresh of the project it names. Both
call `Registry.refresh`, which fetches and hard-resets to the remote tip, so
running them twice, or concurrently with a request, is harmless.
"""
from __future__ import annotations
import hashlib
import hmac
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from . import inbox
from .registry import Registry
from .writer import edit

logger = logging.getLogger(__name__)


def refresh_all(registry: Registry) -> dict[str, str | None]:
    """Refresh every project concurrently. Returns {key: error or None}; one
    failing repository never stops the others."""
    keys = registry.keys()
    if not keys:
        return {}

    def one(key: str) -> tuple[str, str | None]:
        try:
            registry.refresh(key)
            return key, None
        except Exception as exc:  # recorded in the project's status by refresh()
            logger.warning("refresh of %s failed: %s", key, exc)
            return key, str(exc) or exc.__class__.__name__

    with ThreadPoolExecutor(max_workers=min(8, len(keys))) as pool:
        return dict(pool.map(one, keys))


def poll_loop(registry: Registry, interval: float, stop: threading.Event) -> None:
    while not stop.wait(max(interval, 5)):
        try:
            refresh_all(registry)
        except Exception:
            logger.exception("project poll failed; continuing")


def sweep_all(registry: Registry) -> dict[str, list[str] | str]:
    """Archive inbox items untriaged for ARCHIVE_DAYS, in every project, one commit each
    (none when nothing is due). Returns {key: archived ids, or the error}."""
    out: dict[str, list[str] | str] = {}
    for key in registry.keys():
        moved: list[str] = []
        planning_dir = registry.get(key).spec.planning_dir
        try:
            def fn(checkout, moved=moved, planning_dir=planning_dir):
                moved[:] = inbox.sweep(checkout / planning_dir)
            edit(registry, key, fn, lambda moved=moved: f"inbox: archive {len(moved)} stale item(s)")
            out[key] = moved
        except Exception as exc:  # one unreachable project must not stop the others
            logger.warning("inbox sweep of %s failed: %s", key, exc)
            out[key] = str(exc) or exc.__class__.__name__
    return out


def sweep_loop(registry: Registry, interval: float, stop: threading.Event) -> None:
    while not stop.wait(max(interval, 60)):
        try:
            sweep_all(registry)
        except Exception:
            logger.exception("inbox sweep failed; continuing")


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    """GitHub's X-Hub-Signature-256 check, constant time."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def _norm(repo: str) -> str:
    """owner/name for a GitHub URL or path-like repo, else the string itself."""
    m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", repo)
    return (m.group(1) if m else repo).lower()


def projects_for_repo(registry: Registry, payload: dict) -> list[str]:
    repo = payload.get("repository") or {}
    names = {_norm(v) for v in (repo.get("full_name"), repo.get("clone_url"), repo.get("ssh_url")) if v}
    return [k for k in registry.keys() if _norm(registry.get(k).spec.repo) in names]


def handle_push_event(registry: Registry, payload: dict) -> dict[str, str | None]:
    """Refresh the projects whose repository the event names."""
    keys = projects_for_repo(registry, payload)
    result = {}
    for key in keys:
        try:
            registry.refresh(key)
            result[key] = None
        except Exception as exc:
            result[key] = str(exc)
    return result
