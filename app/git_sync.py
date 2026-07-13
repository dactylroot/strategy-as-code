from __future__ import annotations
import logging
import os
import subprocess
import threading
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 5

_timer_lock = threading.Lock()
_debounce_timer: threading.Timer | None = None
_git_lock = threading.Lock()  # serializes actual git operations against the repo


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
        logger.warning("git-sync enabled but %s is not a git repository; skipping", repo_dir)
        return False

    try:
        with _git_lock:
            return _sync_locked(repo_dir, reason)
    except Exception:
        logger.exception("git content-sync failed (reason=%s)", reason)
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

    # Best-effort fetch - the branch may not exist yet on the remote (first run).
    _git(repo_dir, ["fetch", remote, branch], check=False)

    parent: str | None = None
    rev = subprocess.run(
        ["git", "rev-parse", f"{remote}/{branch}"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if rev.returncode == 0:
        parent = rev.stdout.strip()

    scratch_index = repo_dir / ".git" / "content-sync-index"
    env = {**os.environ, "GIT_INDEX_FILE": str(scratch_index)}
    try:
        if parent:
            _git(repo_dir, ["read-tree", parent], env=env)
        else:
            _git(repo_dir, ["read-tree", "--empty"], env=env)

        for rel_path in paths:
            if (repo_dir / rel_path).exists():
                _git(repo_dir, ["add", "--", rel_path], env=env)
            else:
                _git(repo_dir, ["rm", "--cached", "--ignore-unmatch", "--", rel_path], env=env)

        tree = _git(repo_dir, ["write-tree"], env=env)

        if parent:
            parent_tree = _git(repo_dir, ["rev-parse", f"{parent}^{{tree}}"])
            if tree == parent_tree:
                return False  # nothing changed since last sync

        commit_args = ["commit-tree", tree, "-m", f"content-sync: {reason}"]
        if parent:
            commit_args += ["-p", parent]
        commit = _git(repo_dir, commit_args)

        _git(repo_dir, ["push", remote, f"{commit}:refs/heads/{branch}"])
        logger.info("git-sync: pushed %s to %s/%s (reason=%s)", commit[:8], remote, branch, reason)
        return True
    finally:
        scratch_index.unlink(missing_ok=True)
