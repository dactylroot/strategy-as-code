"""What a contributor (a Power User of the project's app) is shown.

Features are shown in full. Bugs are filtered: a bug shows only when it has a WBS reference or the contributor
submitted it, and only the contributor's own bugs show their Notes. The Closed table has no WBS column, so a
closed bug shows only when it is the contributor's own. A promoted row is recognised as theirs by the
`(inbox <id>)` marker promotion leaves in its Notes.
"""
from __future__ import annotations
import json

from . import inbox
from .models import BugDoc, BugItem, ClosedBug


def _is_own(notes: str, own_ids: set[str]) -> bool:
    return any(inbox._marker(i) in notes for i in own_ids)


def visible_bugs(doc: BugDoc, own_ids: set[str]) -> tuple[list[BugItem], list[ClosedBug]]:
    active = []
    for bug in doc.active:
        if _is_own(bug.notes, own_ids):
            active.append(bug)
        elif bug.wbs_ref:
            active.append(bug.model_copy(update={"notes": ""}))
    closed = [bug for bug in doc.closed if _is_own(bug.notes, own_ids)]
    return active, closed


def script_json(value) -> str:
    """JSON for a <script> block: `<` is escaped so repository text can never close the tag."""
    return json.dumps(value).replace("<", "\\u003c")
