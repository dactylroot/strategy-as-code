from __future__ import annotations
import logging

import httpx

from .config import settings
from .parsers import bugs as bugs_parser

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"


def create_missing_issues() -> None:
    """One-way mirror: for every bug row in BUGS.MD lacking a linked GitHub
    Issue, create one and backfill the row's GH Issue column. Never reads
    Issues back into BUGS.MD - it stays the authoritative source, this is a
    creation-time export only. Covers rows written by either this app's own
    UI or an embedding host's own bug-report feature (e.g. renewals'
    bug_report_service.py), since it just scans BUGS.MD's current state."""
    if not settings.github_repo or not settings.git_token:
        return

    path = settings.bugs_md
    if not path.exists():
        return

    doc = bugs_parser.parse(path)
    for bug in doc.active:
        if bug.gh_issue:
            continue
        issue_ref = _create_issue(bug)
        if issue_ref is None:
            continue
        if not bugs_parser.set_gh_issue(path, bug.id, issue_ref):
            logger.warning(
                "github_issues: created issue %s for bug %s but failed to backfill BUGS.MD",
                issue_ref, bug.id,
            )


def _create_issue(bug) -> str | None:
    body = (
        f"{bug.notes}\n\n"
        "---\n"
        f"Mirrored from BUGS.MD row {bug.id} (Project Planning) - this is a one-way "
        "mirror, edit status/notes in Project Planning rather than here."
    )
    payload = {
        "title": bug.title,
        "body": body,
        "labels": [f"severity-{bug.severity.value.lower()}"],
    }
    try:
        resp = httpx.post(
            f"{_API_BASE}/repos/{settings.github_repo}/issues",
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.git_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        return str(resp.json()["number"])
    except Exception:
        logger.exception("github_issues: failed to create issue for bug %s", bug.id)
        return None
