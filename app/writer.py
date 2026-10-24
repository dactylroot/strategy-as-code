"""The single write path for member projects' planning files.

Every writer (web UI, `strategy` CLI, Claude skill) goes through `edit()`:
lock the project, sync its clone to the remote tip, let the caller change files
in the fresh checkout, commit as the actor, push as a fast-forward. If the push
is rejected because someone else pushed first, the whole thing is redone on the
new tip, so the caller's change is always applied to current content (a
row-level merge by construction, given text-to-text transforms like
`parsers.product.transform_feature_status`).
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import pullrequest
from .registry import GitError, Project, Registry, RegistryError

MAX_ATTEMPTS = 3
_REJECTED = ("rejected", "non-fast-forward", "fetch first", "failed to update ref")


class WriteError(RegistryError):
    pass


class EditAborted(Exception):
    """Raised by an edit function to abandon the write without it counting as a sync
    failure (e.g. the request was invalid). The original error is the __cause__."""


class WriteConflict(WriteError):
    """The push kept losing to concurrent writers."""


@dataclass(frozen=True)
class Actor:
    name: str
    email: str


@dataclass
class WriteResult:
    project: str
    changed: bool          # False when the edit produced no difference
    sha: str               # commit pushed, or the unchanged tip
    branch: str
    attempts: int


DEFAULT_COMMITTER = Actor("strategy-as-code", "noreply@strategy-as-code")


def _commit_env(actor: Actor | None, committer: Actor) -> dict:
    author = actor or committer
    return {
        "GIT_AUTHOR_NAME": author.name, "GIT_AUTHOR_EMAIL": author.email,
        "GIT_COMMITTER_NAME": committer.name, "GIT_COMMITTER_EMAIL": committer.email,
    }


def edit(
    registry: Registry,
    key: str,
    fn: Callable[[Path], None],
    message: str | Callable[[], str],
    actor: Actor | None = None,
    committer: Actor = DEFAULT_COMMITTER,
    max_attempts: int = MAX_ATTEMPTS,
) -> WriteResult:
    """Apply `fn(checkout_dir)` to a fresh checkout of project `key` and push it.

    `message` may be a callable; it is evaluated after `fn` ran, so the message
    can describe what `fn` produced (e.g. the WBS code it assigned).

    `fn` may be called more than once (once per attempt), so it must derive its
    result from the files it is handed, not from state captured earlier. An
    exception from `fn` aborts the write and leaves the remote untouched.
    """
    project = registry.ensure_clone(key)
    last_err = ""
    for attempt in range(1, max_attempts + 1):
        with project.locked():
            try:
                project.sync()
                fn(project.path)
                text = message() if callable(message) else message
                result = _commit_and_push(project, text, actor, committer, attempt)
                project.status.last_ok = True
                project.status.last_error = None
                return result
            except _PushRejected as rej:
                last_err = rej.stderr
            except EditAborted:
                raise
            except Exception as exc:
                project.status.last_ok = False
                project.status.last_error = str(exc)
                raise
    msg = f"{key}: push rejected {max_attempts} times: {last_err}"
    project.status.last_ok = False
    project.status.last_error = msg
    raise WriteConflict(msg)


class _PushRejected(Exception):
    def __init__(self, stderr: str):
        self.stderr = stderr


def _commit_and_push(project: Project, message: str, actor: Actor | None,
                     committer: Actor, attempt: int) -> WriteResult:
    base_branch = project.default_branch()
    branch = project.target_branch or base_branch
    project.git("add", "-A")
    if not project.git("status", "--porcelain"):
        return WriteResult(project.key, False, project.git("rev-parse", "HEAD"), branch, attempt)
    project.git("commit", "-q", "-m", message, env=_commit_env(actor, committer))
    sha = project.git("rev-parse", "HEAD")
    try:
        project.git("push", "origin", f"{branch}:{branch}")
    except GitError as exc:
        if any(token in exc.stderr for token in _REJECTED):
            raise _PushRejected(exc.stderr) from exc
        raise WriteError(f"{project.key}: push failed: {exc.stderr}") from exc
    project.status.sha = sha
    project.status.last_push = time.time()
    project.status.pushes += 1
    pullrequest.request_sync(project, base_branch)
    return WriteResult(project.key, True, sha, branch, attempt)


def edit_file(
    registry: Registry,
    key: str,
    rel_path: str,
    text_fn: Callable[[str], str],
    message: str,
    actor: Actor | None = None,
    **kwargs,
) -> WriteResult:
    """Convenience for the common case: one planning file, one text transform
    (any `transform_*` function with its arguments bound)."""
    def apply(checkout: Path) -> None:
        target = checkout / rel_path
        if not target.is_file():
            raise WriteError(f"{key}: {rel_path} does not exist")
        original = target.read_text(encoding="utf-8")
        updated = text_fn(original)
        if updated != original:
            target.write_text(updated, encoding="utf-8")

    return edit(registry, key, apply, message, actor, **kwargs)
