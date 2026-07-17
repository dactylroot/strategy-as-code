from __future__ import annotations
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 5

_timer_lock = threading.Lock()
_debounce_timer: threading.Timer | None = None
_git_lock = threading.Lock()  # serializes actual git operations against the repo

# Last-known content-sync health, so a silently-failing push (bad auth, git
# `safe.directory`/ownership, missing remote) becomes observable instead of
# only landing in the logs. Exposed via get_status() and the /api/sync-status
# endpoint. sync_now() still never raises - this only records what happened.
_status_lock = threading.Lock()
_status: dict = {
    "last_attempt": None,   # epoch seconds of the most recent sync attempt
    "last_success": None,   # epoch seconds of the most recent clean completion
    "last_push": None,      # epoch seconds of the most recent actual push
    "last_error": None,     # str of the most recent failure, or None
    "last_ok": None,        # True/False of the most recent attempt (None = never)
    "pushes": 0,            # count of commits actually pushed this process
}


def _record(*, ok: bool, error: str | None, pushed: bool = False) -> None:
    now = time.time()
    with _status_lock:
        _status["last_attempt"] = now
        _status["last_ok"] = ok
        _status["last_error"] = error
        if ok:
            _status["last_success"] = now
        if pushed:
            _status["pushes"] += 1
            _status["last_push"] = now


def get_status() -> dict:
    """Snapshot of content-sync health for operators/UI. `healthy` is False only
    when git-sync is enabled and the last attempt actually failed."""
    with _status_lock:
        snapshot = dict(_status)
    snapshot["enabled"] = settings.git_sync_enabled
    snapshot["healthy"] = (not snapshot["enabled"]) or (snapshot["last_ok"] is not False)
    return snapshot


def schedule_sync(reason: str = "save") -> None:
    """Debounce rapid successive saves into a single sync a few seconds later."""
    if not settings.git_sync_enabled:
        return
    global _debounce_timer
    with _timer_lock:
        if _debounce_timer is not None:
            _debounce_timer.cancel()
        _debounce_timer = threading.Timer(_DEBOUNCE_SECONDS, _run_scheduled, args=(reason,))
        _debounce_timer.daemon = True
        _debounce_timer.start()


def _run_scheduled(reason: str) -> None:
    try:
        sync_now(reason)
    except Exception:
        logger.exception("git content-sync failed (reason=%s)", reason)


def sync_now(reason: str = "manual") -> bool:
    """Commit and push the configured paths to the content-sync branch via git
    plumbing, without touching the shared working tree's checked-out branch or
    index. Returns True if a new commit was pushed, False if nothing changed or
    sync is disabled/unavailable. Never raises - a sync problem must never
    surface as a failed save.
    """
    if not settings.git_sync_enabled:
        return False

    repo_dir = settings.project_dir
    if not (repo_dir / ".git").exists():
        msg = f"{repo_dir} is not a git repository"
        logger.warning("git-sync enabled but %s; skipping", msg)
        _record(ok=False, error=msg)
        return False

    try:
        with _git_lock:
            pushed = _sync_locked(repo_dir, reason)
        _record(ok=True, error=None, pushed=pushed)
        return pushed
    except Exception as exc:
        logger.exception("git content-sync failed (reason=%s)", reason)
        _record(ok=False, error=str(exc))
        return False


def _git(repo_dir: Path, args: list[str], env: dict | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_dir, env=env, capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _sync_locked(repo_dir: Path, reason: str) -> bool:
    remote = settings.git_sync_remote
    branch = settings.git_sync_branch
    paths = settings.git_sync_paths
    if not paths:
        return False

    # Best-effort fetch, used only for the no-op check below - content-sync is
    # a rolling single-commit snapshot of the latest state, never an
    # append-only log (nobody reads its history; only its current file
    # contents matter - see t3-renewals-manager's reconcile-content-sync.yml),
    # so a sync never builds on a previous commit as a parent. A missing or
    # unfetchable remote branch (deleted, never created, this fetch failing)
    # just means we can't skip a no-op push; it can no longer produce a
    # silent orphan; every sync already replaces the branch outright on purpose.
    _git(repo_dir, ["fetch", remote, branch], check=False)
    current_tip_tree: str | None = None
    rev = subprocess.run(
        ["git", "rev-parse", f"{remote}/{branch}^{{tree}}"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if rev.returncode == 0:
        current_tip_tree = rev.stdout.strip()

    scratch_index = repo_dir / ".git" / "content-sync-index"
    env = {**os.environ, "GIT_INDEX_FILE": str(scratch_index)}
    try:
        _git(repo_dir, ["read-tree", "--empty"], env=env)

        for rel_path in paths:
            if (repo_dir / rel_path).exists():
                _git(repo_dir, ["add", "--", rel_path], env=env)
            else:
                _git(repo_dir, ["rm", "--cached", "--ignore-unmatch", "--", rel_path], env=env)

        tree = _git(repo_dir, ["write-tree"], env=env)

        if tree == current_tip_tree:
            return False  # nothing changed since last sync

        commit = _git(repo_dir, ["commit-tree", tree, "-m", f"content-sync: {reason}"])

        _git(repo_dir, ["push", "--force", remote, f"{commit}:refs/heads/{branch}"])
        logger.info("git-sync: replaced %s/%s with %s (reason=%s)", remote, branch, commit[:8], reason)
        return True
    finally:
        scratch_index.unlink(missing_ok=True)
