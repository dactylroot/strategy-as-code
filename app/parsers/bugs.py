from __future__ import annotations
import os
import re
import threading
from datetime import date
from pathlib import Path

from ..models import BugItem, BugSeverity, BugStatus, BugCreate, BugUpdate

_locks: dict[Path, threading.Lock] = {}
_locks_mutex = threading.Lock()

_EMPTY_BUGS = """\
# Bugs

## Active

| ID | Title | Severity | Status | Notes | WBS | Fix Version | Owner | UAT Confirmed |
|----|-------|----------|--------|-------|-----|-------------|-------|----------------|

## Resolved

| ID | Title | Resolved In | Date |
|----|-------|-------------|------|
"""


def _lock_for(path: Path) -> threading.Lock:
    with _locks_mutex:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _ensure_exists(path: Path) -> None:
    if not path.exists():
        path.write_text(_EMPTY_BUGS, encoding="utf-8")


def _parse_table_rows(block: str) -> list[list[str]]:
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if not cells or not cells[0] or set(cells[0]) <= {"-", " "}:
            continue
        if cells[0].lower() in ("id",):
            continue
        rows.append(cells)
    return rows


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
    from ..models import BugDoc, ResolvedBug
    active: list[BugItem] = []
    resolved: list[ResolvedBug] = []

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
            try:
                status = BugStatus(row[3])
            except ValueError:
                status = BugStatus.open
            wbs_ref = row[5].strip() if len(row) > 5 and row[5].strip() else None
            fix_version = row[6].strip() if len(row) > 6 and row[6].strip() else None
            owner = row[7].strip() if len(row) > 7 and row[7].strip() else None
            uat_confirmed = len(row) > 8 and row[8].strip().lower() in ("yes", "true")
            active.append(BugItem(
                id=bug_id, title=row[1], severity=severity,
                status=status, notes=row[4], wbs_ref=wbs_ref, fix_version=fix_version,
                owner=owner, uat_confirmed=uat_confirmed,
            ))

    resolved_m = re.search(r"\n## Resolved\n(.*?)(?=\n## |\Z)", "\n" + text, re.DOTALL)
    if resolved_m:
        for row in _parse_table_rows(resolved_m.group(1)):
            if len(row) < 4:
                continue
            try:
                bug_id = int(row[0])
            except ValueError:
                continue
            resolved.append(ResolvedBug(id=bug_id, title=row[1], resolved_in=row[2], date=row[3]))

    return BugDoc(raw_text=text, active=active, resolved=resolved)


def _next_id(active, resolved) -> int:
    all_ids = [b.id for b in active] + [r.id for r in resolved]
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


def resolve_bug(path: Path, bug_id: int, resolved_in: str = "", today: str | None = None) -> None:
    lock = _lock_for(path)
    with lock:
        _ensure_exists(path)
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_resolve_bug(text, bug_id, resolved_in, today))


# ── Pure transform functions (text-in / text-out, no I/O) ────────────────────

def transform_add_bug(text: str, req: BugCreate) -> tuple[str, BugItem]:
    if not text.strip():
        text = _EMPTY_BUGS
    doc = _parse_text(text)
    new_id = _next_id(doc.active, doc.resolved)
    wbs_col = req.wbs_ref or ""
    owner_col = req.owner or ""
    new_row = f"| {new_id} | {req.title} | {req.severity.value} | Open | {req.notes} | {wbs_col} |  | {owner_col} |  |"
    insert_pos = _find_section_last_row(text, "## Active")
    new_text = text[:insert_pos] + "\n" + new_row + text[insert_pos:]
    return new_text, BugItem(id=new_id, title=req.title, severity=req.severity,
                             status=BugStatus.open, notes=req.notes, wbs_ref=req.wbs_ref,
                             owner=req.owner)


def transform_update_bug(text: str, bug_id: int, req: BugUpdate) -> tuple[str, BugItem]:
    if not text.strip():
        text = _EMPTY_BUGS
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
    pattern  = rf"^\| {bug_id} \|[^\n]+\|$"
    new_row  = (f"| {bug_id} | {new_title} | {new_severity.value} | {new_status.value} | {new_notes} | "
                f"{wbs_col} | {fix_ver_col} | {owner_col} | {uat_col} |")
    new_text, n = re.subn(pattern, new_row, text, flags=re.MULTILINE)
    if n != 1:
        raise ValueError(f"Expected 1 match for bug {bug_id}, got {n}")
    return new_text, BugItem(id=bug_id, title=new_title, severity=new_severity,
                             status=new_status, notes=new_notes, wbs_ref=bug.wbs_ref,
                             fix_version=new_fix_ver, owner=new_owner, uat_confirmed=new_uat)


def transform_resolve_bug(text: str, bug_id: int, resolved_in: str = "", today: str | None = None) -> str:
    if not text.strip():
        text = _EMPTY_BUGS
    doc = _parse_text(text)
    bug = next((b for b in doc.active if b.id == bug_id), None)
    if bug is None:
        raise ValueError(f"Bug {bug_id} not found")
    date_str = today or str(date.today())
    wbs_col = bug.wbs_ref or ""
    fix_ver_col = bug.fix_version or ""
    owner_col = bug.owner or ""
    uat_col = "Yes" if bug.uat_confirmed else ""
    pattern = rf"^\| {bug_id} \|[^\n]+\|$"
    resolved_row = (f"| {bug_id} | {bug.title} | {bug.severity.value} | Resolved | {bug.notes} | "
                    f"{wbs_col} | {fix_ver_col} | {owner_col} | {uat_col} |")
    text, n = re.subn(pattern, resolved_row, text, flags=re.MULTILINE)
    if n != 1:
        raise ValueError(f"Expected 1 match for bug {bug_id}, got {n}")
    new_resolved_row = f"| {bug_id} | {bug.title} | {resolved_in} | {date_str} |"
    try:
        insert_pos = _find_section_last_row(text, "## Resolved")
        text = text[:insert_pos] + "\n" + new_resolved_row + text[insert_pos:]
    except ValueError:
        pass
    return text
