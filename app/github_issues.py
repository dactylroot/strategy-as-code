from __future__ import annotations
import logging
import re
from pathlib import Path

import httpx

from .config import settings
from .parsers import bugs as bugs_parser

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_UPLOADS_BASE = "https://uploads.github.com"

# Tag of a dedicated, never-published GitHub Release used purely as binary
# storage for mirrored bug screenshots. GitHub has no public API for
# attaching an image directly to an Issue body (the drag-and-drop CDN is an
# internal, session-authenticated endpoint) - Release assets are the closest
# real, documented API for hosting a file in the repo without a git commit.
_ASSET_RELEASE_TAG = "bug-screenshots"

_SCREENSHOT_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
}

# Embedded (invisible, since it's an HTML comment) in every mirrored Issue's
# body so GitHub itself - not just BUGS.MD's GH Issue column - can answer
# "does this bug already have an Issue?". BUGS.MD is git-tracked and gets
# reset/restored by CI on deploy; if that restore ever no-ops (as happened
# 2026-07-14, see strategy-as-code history), the GH Issue column can revert
# to blank even though the Issue already exists. Re-scanning Issues each
# pass makes create_missing_issues idempotent regardless of BUGS.MD's state.
_DEDUP_MARKER_RE = re.compile(r"<!-- strategy-as-code:bugs-md-id=(\d+) -->")


def _dedup_marker(bug_id: int) -> str:
    return f"<!-- strategy-as-code:bugs-md-id={bug_id} -->"


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.git_token}",
        "Accept": "application/vnd.github+json",
    }


def _scan_mirrored_issues() -> dict[int, dict]:
    """Page through all Issues (any state) in the repo and return
    {bug_id: {"number": str, "state": "open"|"closed"}} for every one
    carrying our dedup marker. Uses the plain issues-list endpoint rather
    than GitHub's search API, since search indexing lags writes and isn't a
    reliable dedup check for an Issue created moments earlier in the same
    pass. Shared by create_missing_issues (to avoid duplicate creation) and
    close_resolved_issues (to avoid re-closing an already-closed Issue)."""
    found: dict[int, dict] = {}
    page = 1
    try:
        while True:
            resp = httpx.get(
                f"{_API_BASE}/repos/{settings.github_repo}/issues",
                params={"state": "all", "per_page": 100, "page": page},
                headers=_auth_headers(), timeout=10.0,
            )
            resp.raise_for_status()
            items = resp.json()
            if not items:
                break
            for item in items:
                m = _DEDUP_MARKER_RE.search(item.get("body") or "")
                if m:
                    found[int(m.group(1))] = {"number": str(item["number"]), "state": item["state"]}
            if len(items) < 100:
                break
            page += 1
    except Exception:
        logger.exception("github_issues: failed to list existing issues for dedup scan")
    return found


def create_missing_issues() -> None:
    """One-way mirror: for every bug row in BUGS.MD lacking a linked GitHub
    Issue, create one and backfill the row's GH Issue column. Never reads
    Issues back into BUGS.MD - it stays the authoritative source, this is a
    creation-time export only. Covers rows written by either this app's own
    UI or an embedding host's own bug-report feature (e.g. renewals'
    bug_report_service.py), since it just scans BUGS.MD's current state.

    Before creating anything, cross-checks GitHub's own Issues for the dedup
    marker (see _scan_mirrored_issues) so a bug that already has a mirrored
    Issue is never duplicated even if BUGS.MD's GH Issue column was lost or
    reverted."""
    if not settings.github_repo or not settings.git_token:
        return

    path = settings.bugs_md
    if not path.exists():
        return

    doc = bugs_parser.parse(path)
    pending = [bug for bug in doc.active if not bug.gh_issue]
    if not pending:
        return

    mirrored = _scan_mirrored_issues()

    for bug in pending:
        entry = mirrored.get(bug.id)
        issue_ref = entry["number"] if entry else _create_issue(bug)
        if issue_ref is None:
            continue
        if not bugs_parser.set_gh_issue(path, bug.id, issue_ref):
            logger.warning(
                "github_issues: created issue %s for bug %s but failed to backfill BUGS.MD",
                issue_ref, bug.id,
            )


def close_resolved_issues() -> None:
    """Companion half of the mirror: for every Resolved bug in BUGS.MD that
    has a linked Issue, close that Issue if it isn't already closed. Still
    one-way and still never writes Issue state back into BUGS.MD - this only
    flips GitHub's state field, matching a status change that already
    happened in Project Planning."""
    if not settings.github_repo or not settings.git_token:
        return

    path = settings.bugs_md
    if not path.exists():
        return

    doc = bugs_parser.parse(path)
    resolved_with_issue = [bug for bug in doc.resolved if bug.gh_issue]
    if not resolved_with_issue:
        return

    mirrored = _scan_mirrored_issues()

    for bug in resolved_with_issue:
        entry = mirrored.get(bug.id)
        if entry and entry["state"] == "closed":
            continue
        issue_number = entry["number"] if entry else bug.gh_issue
        _close_issue(issue_number, bug.id)


def _close_issue(issue_number: str, bug_id: int) -> None:
    try:
        resp = httpx.patch(
            f"{_API_BASE}/repos/{settings.github_repo}/issues/{issue_number}",
            json={"state": "closed", "state_reason": "completed"},
            headers=_auth_headers(), timeout=10.0,
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("github_issues: failed to close issue %s for resolved bug %s", issue_number, bug_id)


def _find_screenshot(bug_id: int) -> Path | None:
    matches = list((settings.project_dir / ".screenshots").glob(f"bug_{bug_id}.*"))
    return matches[0] if matches else None


def _asset_release_id() -> int | None:
    """Return the id of the dedicated screenshot-storage release, creating it
    (as a draft, so it never shows up as a real release) on first use."""
    try:
        resp = httpx.get(
            f"{_API_BASE}/repos/{settings.github_repo}/releases/tags/{_ASSET_RELEASE_TAG}",
            headers=_auth_headers(), timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json()["id"]

        resp = httpx.post(
            f"{_API_BASE}/repos/{settings.github_repo}/releases",
            json={
                "tag_name": _ASSET_RELEASE_TAG,
                "name": "Bug screenshots (internal asset store)",
                "body": "Not a real release - binary storage for screenshots linked from mirrored bug Issues.",
                "draft": True,
                "prerelease": True,
            },
            headers=_auth_headers(), timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["id"]
    except Exception:
        logger.exception("github_issues: failed to get/create screenshot asset release")
        return None


def _upload_screenshot_asset(bug_id: int, path: Path) -> str | None:
    """Upload path as a Release asset and return its browser_download_url.
    Reuses an already-uploaded asset of the same name instead of erroring,
    so a retry after a partial failure (e.g. issue created but BUGS.MD
    backfill failed) doesn't hit GitHub's duplicate-asset-name 422."""
    release_id = _asset_release_id()
    if release_id is None:
        return None
    asset_name = f"bug-{bug_id}{path.suffix.lower()}"
    try:
        existing = httpx.get(
            f"{_API_BASE}/repos/{settings.github_repo}/releases/{release_id}/assets",
            headers=_auth_headers(), timeout=10.0,
        )
        if existing.status_code == 200:
            for asset in existing.json():
                if asset["name"] == asset_name:
                    return asset["browser_download_url"]

        resp = httpx.post(
            f"{_UPLOADS_BASE}/repos/{settings.github_repo}/releases/{release_id}/assets",
            params={"name": asset_name},
            content=path.read_bytes(),
            headers={
                **_auth_headers(),
                "Content-Type": _SCREENSHOT_MIME.get(path.suffix.lower(), "application/octet-stream"),
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["browser_download_url"]
    except Exception:
        logger.exception("github_issues: failed to upload screenshot for bug %s", bug_id)
        return None


def _create_issue(bug) -> str | None:
    header_lines = []
    if bug.owner:
        header_lines.append(f"**Reported by:** {bug.owner}")
    if bug.wbs_ref:
        header_lines.append(f"**WBS:** {bug.wbs_ref}")

    screenshot_path = _find_screenshot(bug.id)
    if screenshot_path:
        asset_url = _upload_screenshot_asset(bug.id, screenshot_path)
        if asset_url:
            header_lines.append(f"**Screenshot:** [{screenshot_path.name}]({asset_url})")

    body_parts = []
    if header_lines:
        body_parts.append("\n".join(header_lines))
    body_parts.append(bug.notes)
    body_parts.append(
        "---\n"
        f"Mirrored from BUGS.MD row {bug.id} (Project Planning) - this is a one-way "
        "mirror, edit status/notes in Project Planning rather than here.\n"
        f"{_dedup_marker(bug.id)}"
    )
    body = "\n\n".join(body_parts)

    payload = {
        "title": bug.title,
        "body": body,
        "labels": [f"severity-{bug.severity.value.lower()}"],
    }
    try:
        resp = httpx.post(
            f"{_API_BASE}/repos/{settings.github_repo}/issues",
            json=payload,
            headers=_auth_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        return str(resp.json()["number"])
    except Exception:
        logger.exception("github_issues: failed to create issue for bug %s", bug.id)
        return None
