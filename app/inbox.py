"""Append-only intake: one file per user submission under `.planning/inbox/`.

A submission is a file of its own, so it cannot collide with a developer's edit
to BUGS.MD or PRODUCT.MD; only its submitter changes it, and only until triage.
A maintainer later promotes it (one commit adds the row and removes the file),
declines it, or marks it a duplicate. The outcome stays as a small record under
`.planning/triaged/` so the submitter can see it (see `mine`); the reason is also
in the commit message. Untriaged files are flagged stale after STALE_DAYS and
moved to `archive/` after ARCHIVE_DAYS; triaged records are pruned after
TRIAGED_DAYS.

Everything here works on a planning directory (the checkout handed to a writer
function); committing is the writer's job.
"""
from __future__ import annotations
import hashlib
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
TRIAGED = Path(".planning") / "triaged"   # outside the inbox, so the inbox folder holds only what awaits triage
STALE_DAYS = 14
ARCHIVE_DAYS = 60
TRIAGED_DAYS = 365
MAX_TITLE = 200
MAX_BODY = 5000
KINDS = ("bug", "feature")
MAX_ATTACHMENT = 5 * 1024 * 1024
_IMAGE_TYPES = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF87a", ".gif"), (b"GIF89a", ".gif"))


class InboxError(ValueError):
    pass


class NotSubmitter(InboxError):
    """Someone other than the submitter tried to change an item."""


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
    outcome: str = ""         # triaged records only: promoted, declined or duplicate
    ref: str = ""             # what a promoted item became ("bug 12", "feature 1.2.3") or the item it duplicates
    reason: str = ""
    triaged: str = ""


def _one_line(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def submitter_tag(submitter: str) -> str:
    """A short stable tag of a submitter's email, ending the id of everything they submit. It lets an app find one
    person's items by file name, without opening every file in the inbox. Other writers of inbox files (the apps'
    own bug-report forms) append the same tag; an id without one simply is not found that way."""
    return "u" + hashlib.sha256(submitter.strip().lower().encode()).hexdigest()[:8]


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "item"


def _clean(kind: str, title: str, body: str, severity: str, wbs: str) -> tuple[str, str, str, str]:
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
    return title, body, severity.lower() if severity else "", wbs


def _render(kind: str, title: str, body: str, submitter: str, created: str, severity: str, wbs: str) -> str:
    head = [f"type: {kind}", f"title: {title}", f"submitter: {submitter}", f"created: {created}"]
    if severity:
        head.append(f"severity: {severity}")
    if wbs:
        head.append(f"wbs: {wbs}")
    return "---\n" + "\n".join(head) + "\n---\n" + body + "\n"


def write_item(root: Path, kind: str, title: str, body: str, submitter: str,
               severity: str = "", wbs: str = "", now: datetime | None = None) -> Item:
    title, body, severity, wbs = _clean(kind, title, body, severity, wbs)
    now = now or _now()
    submitter = _one_line(submitter, 120) or "anonymous"
    item_id = f"{now:%Y%m%dT%H%M%SZ}-{_slug(title)}-{secrets.token_hex(2)}"
    if "@" in submitter:
        item_id += "-" + submitter_tag(submitter)
    folder = root / INBOX
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{item_id}.md"
    path.write_text(_render(kind, title, body, submitter, f"{now:%Y-%m-%dT%H:%M:%SZ}", severity, wbs), encoding="utf-8")
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
        age_days=age, stale=age >= STALE_DAYS and path.parent.name not in ("archive", TRIAGED.name), attachment=shot,
        outcome=meta.get("outcome", ""), ref=meta.get("ref", ""), reason=meta.get("reason", ""),
        triaged=meta.get("triaged", ""),
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


def attach(root: Path, item_id: str, data: bytes) -> str:
    """Store a screenshot next to the item, replacing an earlier one. The type comes
    from the file's own first bytes, never from the name or content type the client
    claims. Returns the stored file name."""
    path = _find(root, item_id)
    if path is None:
        raise InboxError(f"no inbox item {item_id!r}")
    if len(data) > MAX_ATTACHMENT:
        raise InboxError(f"screenshot is larger than {MAX_ATTACHMENT // (1024 * 1024)} MB")
    ext = next((e for magic, e in _IMAGE_TYPES if data.startswith(magic)), None)
    if ext is None:
        raise InboxError("screenshot must be a PNG, JPEG or GIF image")
    for old in path.parent.glob(path.stem + ".*"):
        if old.suffix.lower() != ".md":
            old.unlink()
    target = path.with_suffix(ext)
    target.write_bytes(data)
    return target.name


def attachment_path(root: Path, item_id: str) -> Path | None:
    path = _find(root, item_id)
    if path is None:
        return None
    name = read_item(path).attachment
    return path.parent / name if name else None


def _marker(item_id: str) -> str:
    return f"(inbox {item_id})"


def _row_notes(item: Item) -> str:
    text = _one_line(item.body, 600).replace("|", "/")
    return f"{text} {_marker(item.id)}".strip()


def _remove(root: Path, item_id: str) -> None:
    for p in (root / INBOX).glob(item_id + ".*"):
        p.unlink()


def _triage(root: Path, item: Item, outcome: str, ref: str = "", reason: str = "", now: datetime | None = None) -> None:
    """Keep what became of an item, without its description or screenshot, for the submitter to read."""
    head = [f"type: {item.kind}", f"title: {item.title}", f"submitter: {item.submitter}", f"created: {item.created}",
            f"outcome: {outcome}", f"triaged: {(now or _now()):%Y-%m-%dT%H:%M:%SZ}"]
    if ref:
        head.append(f"ref: {_one_line(ref, 120)}")
    if reason:
        head.append(f"reason: {_one_line(reason, 500)}")
    folder = root / TRIAGED
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{item.id}.md").write_text("---\n" + "\n".join(head) + "\n---\n", encoding="utf-8")


def promote(root: Path, item_id: str, prefix: str = "", value: int | None = None) -> str:
    """Add the item as a bug or feature row and remove its file. A retry that
    finds the row already present only removes the file. Returns what happened."""
    path = _find(root, item_id)
    bugs_file, product_file = root / "BUGS.MD", root / "PRODUCT.MD"
    for f in (bugs_file, product_file):
        if f.exists() and _marker(item_id) in f.read_text(encoding="utf-8"):
            if path is not None:
                _triage(root, read_item(path), "promoted")
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
    _triage(root, item, "promoted", result)
    _remove(root, item_id)
    return result


def decline(root: Path, item_id: str, reason: str = "", duplicate_of: str = "") -> None:
    path = _find(root, item_id)
    if path is None:
        raise InboxError(f"no inbox item {item_id!r}")
    if duplicate_of:
        _triage(root, read_item(path), "duplicate", ref=duplicate_of)
    else:
        _triage(root, read_item(path), "declined", reason=reason)
    _remove(root, item_id)


def _same_person(a: str, b: str) -> bool:
    return bool(a) and a.strip().lower() == b.strip().lower()


def own_item(root: Path, item_id: str, submitter: str) -> tuple[Path, Item]:
    path = _find(root, item_id)
    if path is None:
        raise InboxError(f"no inbox item {item_id!r}")
    item = read_item(path)
    if not _same_person(item.submitter, submitter):
        raise NotSubmitter("only the submitter can change this item")
    return path, item


def update_item(root: Path, item_id: str, submitter: str, title: str | None = None, body: str | None = None,
                severity: str | None = None, wbs: str | None = None) -> Item:
    """The submitter's own edit of an item still waiting for triage; a field left None is kept."""
    path, item = own_item(root, item_id, submitter)
    title, text, sev, ref = _clean(
        item.kind, item.title if title is None else title, item.body if body is None else body,
        item.severity if severity is None else severity, item.wbs if wbs is None else wbs)
    path.write_text(_render(item.kind, title, text, item.submitter, item.created, sev, ref), encoding="utf-8")
    return read_item(path)


def delete_item(root: Path, item_id: str, submitter: str) -> None:
    """The submitter withdraws an item still waiting for triage; no record is kept."""
    own_item(root, item_id, submitter)
    _remove(root, item_id)


def current_state(root: Path, item_id: str) -> str:
    """Where a promoted item stands now, read from the row that carries its marker: a bug's status, a feature's stage."""
    marker = _marker(item_id)
    bugs_file, product_file = root / "BUGS.MD", root / "PRODUCT.MD"
    if bugs_file.exists():
        doc = bugs_parser.parse(bugs_file)
        for bug in doc.active:
            if marker in bug.notes:
                return bug.status.value
        if any(marker in bug.notes for bug in doc.closed):
            return "Closed"
    if product_file.exists():
        for feature in product_parser.parse(product_file).all_features:
            if marker in feature.notes:
                return feature.stage
    return ""


def mine(root: Path, email: str) -> list[dict]:
    """One person's submissions, newest first: those waiting for triage and those with an outcome, each promoted
    one with the current state of the row it became."""
    out = [dict(i.__dict__, outcome="waiting") for i in list_items(root) if _same_person(i.submitter, email)]
    folder = root / TRIAGED
    for path in folder.glob("*.md") if folder.is_dir() else ():
        item = read_item(path)
        if _same_person(item.submitter, email):
            out.append(dict(item.__dict__, state=current_state(root, item.id) if item.outcome == "promoted" else ""))
    return sorted(out, key=lambda r: r["created"], reverse=True)


def own_item_ids(root: Path, email: str) -> set[str]:
    """Ids of everything this person submitted, waiting or triaged: how their promoted rows are recognised."""
    return {r["id"] for r in mine(root, email)}


def _prune_triaged(root: Path, now: datetime | None = None) -> None:
    folder = root / TRIAGED
    for path in folder.glob("*.md") if folder.is_dir() else ():
        try:
            when = datetime.strptime(read_item(path).triaged, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ((now or _now()) - when).days >= TRIAGED_DAYS:
            path.unlink()


def sweep(root: Path, now: datetime | None = None) -> list[str]:
    """Move items untriaged for ARCHIVE_DAYS into archive/ and drop triaged records older than TRIAGED_DAYS.
    Returns the archived ids."""
    _prune_triaged(root, now)
    moved = []
    for item in list_items(root, now=now):
        if item.age_days >= ARCHIVE_DAYS:
            (root / ARCHIVE).mkdir(parents=True, exist_ok=True)
            for p in (root / INBOX).glob(item.id + ".*"):
                p.rename(root / ARCHIVE / p.name)
            moved.append(item.id)
    return moved
