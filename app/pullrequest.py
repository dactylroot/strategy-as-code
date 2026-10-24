"""Keep one open pull request from a branch-mode project's planning branch to its default branch.

Branch mode pushes edits to a `planning` branch so nothing lands on the default branch
unreviewed. This is the other half: after a push, make sure a pull request exists for the
branch and that its description lists what is waiting. A person still merges it; nothing here does.

Only github.com repositories, and only when $GIT_TOKEN is set. Failure is never allowed to
fail a write: the edit is already pushed, and the next push tries again.
"""
from __future__ import annotations
import logging
import os
import re
import threading
from dataclasses import dataclass

import httpx

from .registry import Project

logger = logging.getLogger(__name__)

API = "https://api.github.com"
MAX_LISTED = 30
_REPO_RE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    created: bool


def github_repo(url: str) -> tuple[str, str] | None:
    m = _REPO_RE.search(url)
    return (m.group(1), m.group(2)) if m else None


def _http() -> httpx.Client:
    token = os.environ.get("GIT_TOKEN", "")
    return httpx.Client(base_url=API, timeout=10.0, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})


def enabled(project: Project) -> bool:
    return (project.spec.mode == "branch" and bool(os.environ.get("GIT_TOKEN"))
            and github_repo(project.spec.repo) is not None
            and os.environ.get("PLANNING_PR", "true").strip().lower() not in ("0", "false", "no", "off"))


def waiting(project: Project, base: str) -> list[str]:
    """Subjects of the planning branch's own commits not yet on the default branch (caller holds the lock)."""
    out = project.git("log", f"origin/{base}..HEAD", "--no-merges", "--format=%s (%an)")
    return [l for l in out.splitlines() if l.strip()]


def describe(branch: str, base: str, commits: list[str]) -> tuple[str, str]:
    title = f"Planning updates: {len(commits)} change{'s' if len(commits) != 1 else ''}"
    listed = [f"- {c}" for c in commits[:MAX_LISTED]]
    if len(commits) > MAX_LISTED:
        listed.append(f"- and {len(commits) - MAX_LISTED} more")
    body = (f"Changes made to the planning files through strategy-as-code, waiting on `{branch}` for `{base}`.\n\n"
            + "\n".join(listed)
            + "\n\nThis description is refreshed on every push to the branch. Merge it when the changes are reviewed.")
    return title, body


def ensure_pull_request(project: Project, base: str, commits: list[str], client: httpx.Client | None = None) -> PullRequest | None:
    """Open the pull request for `project`'s planning branch, or refresh the open one's title and description.
    Returns None when there is nothing to propose (no commits waiting, none open). Raises on an API failure."""
    owner, repo = github_repo(project.spec.repo)
    branch = project.target_branch
    own = client is None
    client = client or _http()
    try:
        r = client.get(f"/repos/{owner}/{repo}/pulls", params={"head": f"{owner}:{branch}", "base": base, "state": "open"})
        r.raise_for_status()
        existing = r.json()
        if not commits and not existing:
            return None
        title, body = describe(branch, base, commits)
        if existing:
            number = existing[0]["number"]
            if commits:
                client.patch(f"/repos/{owner}/{repo}/pulls/{number}", json={"title": title, "body": body}).raise_for_status()
            return PullRequest(number, existing[0]["html_url"], False)
        made = client.post(f"/repos/{owner}/{repo}/pulls", json={"title": title, "head": branch, "base": base, "body": body})
        made.raise_for_status()
        data = made.json()
        return PullRequest(data["number"], data["html_url"], True)
    finally:
        if own:
            client.close()


def request_sync(project: Project, base: str) -> None:
    """After a push to the planning branch: refresh its pull request in the background. The commit
    list is read here, under the caller's lock; the network calls happen on a thread."""
    if not enabled(project) or not project.target_branch or project.target_branch == base:
        return
    commits = waiting(project, base)

    def run() -> None:
        try:
            pr = ensure_pull_request(project, base, commits)
            if pr:
                project.status.pr_url = pr.url
                if pr.created:
                    logger.info("opened %s for %s", pr.url, project.key)
        except Exception:
            logger.warning("pull request for %s not updated", project.key, exc_info=True)

    threading.Thread(target=run, daemon=True).start()
