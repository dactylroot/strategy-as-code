from __future__ import annotations
import re
from datetime import date
from pathlib import Path

from ..fileio import _atomic_write, _lock_for
from ..models import BugItem, BugSeverity, BugStatus, BugCreate, BugUpdate

# Legacy status folded into the current lifecycle - "Fix In Progress" was
# retired (it duplicated "Investigating"); rows carrying it read as Resolved.
_LEGACY_STATUS = {"Fix In Progress": BugStatus.resolved}

# The terminal section was renamed "Resolved" -> "Closed" so "Resolved" could
# become an active board column (code-fixed, awaiting UAT). Parse either header
# so files still on the old schema (including host-written ones) keep working.
_CLOSED_SECTION = "## Closed"
_LEGACY_CLOSED_SECTION = "## Resolved"

_EMPTY_BUGS = """\
# Bugs

## Active

| ID | Title | Severity | Status | Notes | WBS | Fix Version | Owner | UAT Confirmed | GH Issue |
|----|-------|----------|--------|-------|-----|-------------|-------|----------------|----------|

## Closed

| ID | Title | Resolved In | Date | GH Issue |
|----|-------|-------------|------|----------|
"""


def _ensure_exists(path: Path) -> None:
    if not path.exists():
        path.write_text(_EMPTY_BUGS, encoding="utf-8")


# Cap on how many physical lines a single row can be recovered from - guards
# against a row whose closing "|" was itself lost (or never existed) eating
# every following row for the rest of the section.
_MAX_ROW_RECOVERY_LINES = 50


def _parse_table_rows(block: str) -> list[list[str]]:
    """Split a table section into rows. Normally one row is one physical line,
    but a note saved before newlines were escaped (see _encode_notes) can
    still have literal line breaks sitting in an old file, splitting its row
    across several lines. Recover those by buffering lines - starting at one
    that opens with "|" but doesn't close with it - until a line closes the
    row, rejoining the buffered lines with "<br>" so the recovered cell reads
    the same as a properly-escaped one. This makes already-corrupted rows
    reappear (and self-heal on their next save) instead of being silently
    dropped."""
    rows = []
    buf: list[str] | None = None
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if buf is None:
            if not line.startswith("|"):
                continue
            if line.endswith("|"):
                row_text = line
            else:
                buf = [line]
                continue
        else:
            buf.append(line)
            if not line.endswith("|") and len(buf) < _MAX_ROW_RECOVERY_LINES:
                continue
            row_text = "<br>".join(buf)
            buf = None
        cells = [c.strip() for c in row_text.split("|")[1:-1]]
        if not cells or not cells[0] or set(cells[0]) <= {"-", " "}:
            continue
        if cells[0].lower() in ("id",):
            continue
        rows.append(cells)
    return rows


def _closed_section_header(text: str) -> str:
    """Return whichever terminal-section header this file actually uses,
    preferring the current "## Closed" and falling back to the legacy
    "## Resolved" so inserts land in the section that already exists."""
    if f"\n{_CLOSED_SECTION}\n" in text:
        return _CLOSED_SECTION
    if f"\n{_LEGACY_CLOSED_SECTION}\n" in text:
        return _LEGACY_CLOSED_SECTION
    return _CLOSED_SECTION


def _find_section_last_row(text: str, section_header: str) -> int:
    header_pos = text.find(f"\n{section_header}\n")
    if header_pos == -1:
        raise ValueError(f"{section_header!r} section not found")
    content_start = header_pos + len(f"\n{section_header}\n")
    next_section = re.search(r"\n## ", text[content_start:])
    content_end = content_start + next_section.start() if next_section else len(text)
    block = text[content_start:content_end]
    last_row_end = 0
    for m in re.finditer(r"^\|.+\|$", block, re.MULTILINE):
        last_row_end = m.end()
    return content_start + last_row_end


def parse(path: Path):
    from ..models import BugDoc
    _ensure_exists(path)
    text = path.read_text(encoding="utf-8")
    return _parse_text(text)


def _parse_text(text: str):
    from ..models import BugDoc, ClosedBug
    active: list[BugItem] = []
    closed: list[ClosedBug] = []

    active_m = re.search(r"\n## Active\n(.*?)(?=\n## |\Z)", "\n" + text, re.DOTALL)
    if active_m:
        for row in _parse_table_rows(active_m.group(1)):
            if len(row) < 5:
                continue
            try:
                bug_id = int(row[0])
            except ValueError:
                continue
            try:
                severity = BugSeverity(row[2])
            except ValueError:
                severity = BugSeverity.medium
            if row[3] in _LEGACY_STATUS:
                status = _LEGACY_STATUS[row[3]]
            else:
                try:
                    status = BugStatus(row[3])
                except ValueError:
                    status = BugStatus.open
            wbs_ref = row[5].strip() if len(row) > 5 and row[5].strip() else None
            fix_version = row[6].strip() if len(row) > 6 and row[6].strip() else None
            owner = row[7].strip() if len(row) > 7 and row[7].strip() else None
            uat_confirmed = len(row) > 8 and row[8].strip().lower() in ("yes", "true")
            gh_issue = row[9].strip() if len(row) > 9 and row[9].strip() else None
            active.append(BugItem(
                id=bug_id, title=row[1], severity=severity,
                status=status, notes=row[4], wbs_ref=wbs_ref, fix_version=fix_version,
                owner=owner, uat_confirmed=uat_confirmed, gh_issue=gh_issue,
            ))

    closed_m = re.search(r"\n## (?:Closed|Resolved)\n(.*?)(?=\n## |\Z)", "\n" + text, re.DOTALL)
    if closed_m:
        for row in _parse_table_rows(closed_m.group(1)):
            if len(row) < 4:
                continue
            try:
                bug_id = int(row[0])
            except ValueError:
                continue
            gh_issue = row[4].strip() if len(row) > 4 and row[4].strip() else None
            closed.append(ClosedBug(id=bug_id, title=row[1], resolved_in=row[2], date=row[3], gh_issue=gh_issue))

    return BugDoc(raw_text=text, active=active, closed=closed)


def _normalize_corrupted_rows(text: str) -> str:
    """Rejoin any table row split across multiple physical lines by an
    unescaped line break saved before notes were escaped (see _encode_notes),
    so the single-line regexes below (title/status/notes updates) can find
    and replace it. Applied at the start of every write so touching the file
    for any reason self-heals rows corrupted by older versions of this code."""
    out_lines = []
    buf: list[str] | None = None
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if buf is None:
            if not line.startswith("|") or line.endswith("|"):
                out_lines.append(raw_line)
            else:
                buf = [line]
            continue
        buf.append(line)
        if line.endswith("|") or len(buf) >= _MAX_ROW_RECOVERY_LINES:
            out_lines.append("<br>".join(buf))
            buf = None
    if buf is not None:
        out_lines.extend(buf)
    return "\n".join(out_lines)


def _encode_notes(notes: str) -> str:
    """Escape literal line breaks so a multi-line note can't split a table
    row across multiple lines - the row parser requires each row to be a
    single line starting and ending with '|'. Mirrors product.py's feature
    notes encoding."""
    return notes.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")


def _next_id(active, closed) -> int:
    all_ids = [b.id for b in active] + [r.id for r in closed]
    return max(all_ids, default=0) + 1


def add_bug(path: Path, req: BugCreate) -> BugItem:
    lock = _lock_for(path)
    with lock:
        _ensure_exists(path)
        text = path.read_text(encoding="utf-8")
        new_text, bug = transform_add_bug(text, req)
        _atomic_write(path, new_text)
        return bug


def update_bug(path: Path, bug_id: int, req: BugUpdate) -> BugItem:
    lock = _lock_for(path)
    with lock:
        _ensure_exists(path)
        text = path.read_text(encoding="utf-8")
        new_text, bug = transform_update_bug(text, bug_id, req)
        _atomic_write(path, new_text)
        return bug


def close_bug(path: Path, bug_id: int, resolved_in: str = "", today: str | None = None) -> None:
    lock = _lock_for(path)
    with lock:
        _ensure_exists(path)
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_close_bug(text, bug_id, resolved_in, today))


def set_gh_issue(path: Path, bug_id: int, issue_ref: str) -> bool:
    """Backfill the GH Issue column for a bug row - called by the periodic
    reconciliation loop (see ../github_issues.py) after mirroring a new bug to a
    real GitHub Issue. Returns False (without raising) if the row can't be
    found/updated unambiguously, so one bad row doesn't abort a whole backfill pass."""
    lock = _lock_for(path)
    with lock:
        _ensure_exists(path)
        text = path.read_text(encoding="utf-8")
        new_text, ok = transform_set_gh_issue(text, bug_id, issue_ref)
        if ok:
            _atomic_write(path, new_text)
        return ok


# ── Pure transform functions (text-in / text-out, no I/O) ────────────────────

def transform_add_bug(text: str, req: BugCreate) -> tuple[str, BugItem]:
    if not text.strip():
        text = _EMPTY_BUGS
    text = _normalize_corrupted_rows(text)
    doc = _parse_text(text)
    new_id = _next_id(doc.active, doc.closed)
    wbs_col = req.wbs_ref or ""
    owner_col = req.owner or ""
    notes_col = _encode_notes(req.notes)
    new_row = f"| {new_id} | {req.title} | {req.severity.value} | Open | {notes_col} | {wbs_col} |  | {owner_col} |  |  |"
    insert_pos = _find_section_last_row(text, "## Active")
    new_text = text[:insert_pos] + "\n" + new_row + text[insert_pos:]
    return new_text, BugItem(id=new_id, title=req.title, severity=req.severity,
                             status=BugStatus.open, notes=req.notes, wbs_ref=req.wbs_ref,
                             owner=req.owner)


def transform_update_bug(text: str, bug_id: int, req: BugUpdate) -> tuple[str, BugItem]:
    if not text.strip():
        text = _EMPTY_BUGS
    text = _normalize_corrupted_rows(text)
    doc = _parse_text(text)
    bug = next((b for b in doc.active if b.id == bug_id), None)
    if bug is None:
        raise ValueError(f"Bug {bug_id} not found")
    new_title    = req.title         if req.title         is not None else bug.title
    new_severity = req.severity      if req.severity      is not None else bug.severity
    new_status   = req.status        if req.status        is not None else bug.status
    new_notes    = req.notes         if req.notes         is not None else bug.notes
    new_fix_ver  = req.fix_version   if req.fix_version   is not None else bug.fix_version
    new_owner    = req.owner         if req.owner         is not None else bug.owner
    new_uat      = req.uat_confirmed if req.uat_confirmed is not None else bug.uat_confirmed
    wbs_col      = bug.wbs_ref or ""
    fix_ver_col  = new_fix_ver or ""
    owner_col    = new_owner or ""
    uat_col      = "Yes" if new_uat else ""
    gh_issue_col = bug.gh_issue or ""
    notes_col    = _encode_notes(new_notes)
    pattern  = rf"^\| {bug_id} \|[^\n]+\|$"
    new_row  = (f"| {bug_id} | {new_title} | {new_severity.value} | {new_status.value} | {notes_col} | "
                f"{wbs_col} | {fix_ver_col} | {owner_col} | {uat_col} | {gh_issue_col} |")
    new_text, n = re.subn(pattern, new_row, text, flags=re.MULTILINE)
    if n != 1:
        raise ValueError(f"Expected 1 match for bug {bug_id}, got {n}")
    return new_text, BugItem(id=bug_id, title=new_title, severity=new_severity,
                             status=new_status, notes=new_notes, wbs_ref=bug.wbs_ref,
                             fix_version=new_fix_ver, owner=new_owner, uat_confirmed=new_uat,
                             gh_issue=bug.gh_issue)


def transform_close_bug(text: str, bug_id: int, resolved_in: str = "", today: str | None = None) -> str:
    if not text.strip():
        text = _EMPTY_BUGS
    text = _normalize_corrupted_rows(text)
    doc = _parse_text(text)
    bug = next((b for b in doc.active if b.id == bug_id), None)
    if bug is None:
        raise ValueError(f"Bug {bug_id} not found")
    date_str = today or str(date.today())
    gh_issue_col = bug.gh_issue or ""
    # Closing removes the bug from the active board entirely (it lives only in
    # the Closed section afterwards) - unlike the active statuses, which keep
    # their row. This is what flips the mirrored GitHub Issue to closed.
    pattern = rf"^\| {bug_id} \|[^\n]+\|$\n?"
    text, n = re.subn(pattern, "", text, count=1, flags=re.MULTILINE)
    if n != 1:
        raise ValueError(f"Expected 1 match for bug {bug_id}, got {n}")
    closed_row = f"| {bug_id} | {bug.title} | {resolved_in} | {date_str} | {gh_issue_col} |"
    try:
        insert_pos = _find_section_last_row(text, _closed_section_header(text))
        text = text[:insert_pos] + "\n" + closed_row + text[insert_pos:]
    except ValueError:
        pass
    return text


def transform_set_gh_issue(text: str, bug_id: int, issue_ref: str) -> tuple[str, bool]:
    text = _normalize_corrupted_rows(text)
    doc = _parse_text(text)
    bug = next((b for b in doc.active if b.id == bug_id), None)
    if bug is None:
        return text, False
    wbs_col = bug.wbs_ref or ""
    fix_ver_col = bug.fix_version or ""
    owner_col = bug.owner or ""
    uat_col = "Yes" if bug.uat_confirmed else ""
    notes_col = _encode_notes(bug.notes)
    pattern = rf"^\| {bug_id} \|[^\n]+\|$"
    new_row = (f"| {bug_id} | {bug.title} | {bug.severity.value} | {bug.status.value} | {notes_col} | "
               f"{wbs_col} | {fix_ver_col} | {owner_col} | {uat_col} | {issue_ref} |")
    new_text, n = re.subn(pattern, new_row, text, count=1, flags=re.MULTILINE)
    return new_text, n == 1
