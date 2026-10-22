"""Append-only intake: one file per user submission under `.planning/inbox/`.

A submission is written once and never edited, so it cannot collide with a
developer's edit to BUGS.MD or PRODUCT.MD. A maintainer later promotes it (one
commit adds the row and removes the file), declines it, or marks it a duplicate;
the reason lives in the commit message. Untriaged files are flagged stale after
STALE_DAYS and moved to `archive/` after ARCHIVE_DAYS.

Everything here works on a planning directory (the checkout handed to a writer
function); committing is the writer's job.
"""
from __future__ import annotations
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import BugCreate, BugSeverity, NewFeature
from .parsers import bugs as bugs_parser
from .parsers import product as product_parser

INBOX = Path(".planning") / "inbox"
ARCHIVE = INBOX / "archive"
STALE_DAYS = 14
ARCHIVE_DAYS = 60
MAX_TITLE = 200
MAX_BODY = 5000
KINDS = ("bug", "feature")


class InboxError(ValueError):
    pass


@dataclass
class Item:
    id: str
    kind: str
    title: str
    submitter: str
    created: str
    severity: str = ""
    wbs: str = ""
    body: str = ""
    archived: bool = False
    age_days: int = 0
    stale: bool = False
    attachment: str = ""      # file name of a screenshot stored next to the item


def _one_line(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "item"


def write_item(root: Path, kind: str, title: str, body: str, submitter: str,
               severity: str = "", wbs: str = "", now: datetime | None = None) -> Item:
    if kind not in KINDS:
        raise InboxError(f"type must be one of {KINDS}")
    title = _one_line(title, MAX_TITLE)
    if not title:
        raise InboxError("a title is required")
    body = (body or "").strip()
    if len(body) > MAX_BODY:
        raise InboxError(f"description is longer than {MAX_BODY} characters")
    if severity and severity.lower() not in {s.value.lower() for s in BugSeverity}:
        raise InboxError(f"unknown severity {severity!r}")
    if wbs and not re.fullmatch(r"\d+(\.\d+)*", wbs):
        raise InboxError(f"invalid WBS reference {wbs!r}")
    now = now or _now()
    item_id = f"{now:%Y%m%dT%H%M%SZ}-{_slug(title)}-{secrets.token_hex(2)}"
    submitter = _one_line(submitter, 120) or "anonymous"
    head = [f"type: {kind}", f"title: {title}", f"submitter: {submitter}",
            f"created: {now:%Y-%m-%dT%H:%M:%SZ}"]
    if severity:
        head.append(f"severity: {severity.lower()}")
    if wbs:
        head.append(f"wbs: {wbs}")
    folder = root / INBOX
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{item_id}.md"
    path.write_text("---\n" + "\n".join(head) + "\n---\n" + body + "\n", encoding="utf-8")
    return read_item(path, now)


def read_item(path: Path, now: datetime | None = None) -> Item:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n?(.*)\Z", text, re.DOTALL)
    meta: dict[str, str] = {}
    body = text
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip()
        body = m.group(2).strip()
    created = meta.get("created", "")
    age = 0
    try:
        age = ((now or _now()) - datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).days
    except ValueError:
        pass
    shot = next((p.name for p in path.parent.glob(path.stem + ".*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif")), "")
    return Item(
        id=path.stem, kind=meta.get("type", "bug"), title=meta.get("title", path.stem),
        submitter=meta.get("submitter", ""), created=created, severity=meta.get("severity", ""),
        wbs=meta.get("wbs", ""), body=body, archived=path.parent.name == "archive",
        age_days=age, stale=age >= STALE_DAYS and path.parent.name != "archive", attachment=shot,
    )


def list_items(root: Path, include_archive: bool = False, now: datetime | None = None) -> list[Item]:
    items = []
    folders = [root / INBOX] + ([root / ARCHIVE] if include_archive else [])
    for folder in folders:
        if folder.is_dir():
            items += [read_item(p, now) for p in sorted(folder.glob("*.md"))]
    return items


def _find(root: Path, item_id: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", item_id):
        raise InboxError("invalid item id")
    p = root / INBOX / f"{item_id}.md"
    return p if p.is_file() else None


def _marker(item_id: str) -> str:
    return f"(inbox {item_id})"


def _row_notes(item: Item) -> str:
    text = _one_line(item.body, 600).replace("|", "/")
    return f"{text} {_marker(item.id)}".strip()


def _remove(root: Path, item_id: str) -> None:
    for p in (root / INBOX).glob(item_id + ".*"):
        p.unlink()


def promote(root: Path, item_id: str, prefix: str = "", value: int | None = None) -> str:
    """Add the item as a bug or feature row and remove its file. A retry that
    finds the row already present only removes the file. Returns what happened."""
    path = _find(root, item_id)
    bugs_file, product_file = root / "BUGS.MD", root / "PRODUCT.MD"
    for f in (bugs_file, product_file):
        if f.exists() and _marker(item_id) in f.read_text(encoding="utf-8"):
            _remove(root, item_id)
            return "already promoted; file removed"
    if path is None:
        raise InboxError(f"no inbox item {item_id!r}")
    item = read_item(path)
    if item.kind == "bug":
        sev = BugSeverity(next(s.value for s in BugSeverity if s.value.lower() == (item.severity or "medium")))
        text = bugs_file.read_text(encoding="utf-8") if bugs_file.exists() else bugs_parser._EMPTY_BUGS
        new_text, bug = bugs_parser.transform_add_bug(
            text, BugCreate(title=item.title.replace("|", "/"), severity=sev, notes=_row_notes(item),
                            wbs_ref=item.wbs or None))
        bugs_file.write_text(new_text, encoding="utf-8")
        if item.attachment:
            shots = root / ".screenshots"
            shots.mkdir(exist_ok=True)
            (path.parent / item.attachment).rename(shots / f"bug_{bug.id}{Path(item.attachment).suffix}")
        result = f"bug {bug.id}"
    else:
        if not prefix:
            raise InboxError("promoting a feature request needs a WBS sub-area (e.g. 1.2)")
        new_text, feat = product_parser.transform_add_feature(
            product_file.read_text(encoding="utf-8"),
            NewFeature(wbs_prefix=prefix, name=item.title.replace("|", "/"), value=value, notes=_row_notes(item)))
        product_file.write_text(new_text, encoding="utf-8")
        result = f"feature {feat.wbs}"
    _remove(root, item_id)
    return result


def decline(root: Path, item_id: str) -> None:
    if _find(root, item_id) is None:
        raise InboxError(f"no inbox item {item_id!r}")
    _remove(root, item_id)


def sweep(root: Path, now: datetime | None = None) -> list[str]:
    """Move items untriaged for ARCHIVE_DAYS into archive/. Returns their ids."""
    moved = []
    for item in list_items(root, now=now):
        if item.age_days >= ARCHIVE_DAYS:
            (root / ARCHIVE).mkdir(parents=True, exist_ok=True)
            for p in (root / INBOX).glob(item.id + ".*"):
                p.rename(root / ARCHIVE / p.name)
            moved.append(item.id)
    return moved
