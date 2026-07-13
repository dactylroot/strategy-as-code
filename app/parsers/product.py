from __future__ import annotations
import re
from pathlib import Path

from ..fileio import _atomic_write, _lock_for
from ..models import (
    Feature, FeatureStatus, WBSArea, WBSSubArea,
    ScopeItem, ScopeGroup, UserPersona, ProductDoc, NewFeature, FeatureUpdate,
)

# Scoped/Scored are matched here for backward compatibility with rows written
# before those became derived (never stored) states - see _normalize_status_str.
_STATUS_PAT = r"(?:Released|Live|Planned|Gap|Idea|Scoped|Scored|In-Progress)"

_LEGACY_STATUS_ALIASES = {"Scoped": "Idea", "Scored": "Idea"}


def _normalize_status_str(status_str: str) -> str:
    """Map legacy literal Scoped/Scored text to Idea; Scoped/Scored are now
    derived from WBS/Notes/Value/Effort rather than stored explicitly."""
    return _LEGACY_STATUS_ALIASES.get(status_str, status_str)


def parse(path: Path) -> ProductDoc:
    text = path.read_text(encoding="utf-8")
    return _parse_text(text)


def _parse_text(text: str) -> ProductDoc:
    # Title - strip common suffixes like " - Product Overview", " - Overview", etc.
    title_m = re.match(r"^# (.+)", text)
    raw_title = title_m.group(1).strip() if title_m else ""
    title = re.sub(r"\s*[-–]\s*(Product\s+)?Overview\s*$", "", raw_title, flags=re.IGNORECASE).strip()

    # Split into ## sections; capture heading and content
    sections: dict[str, str] = {}
    for m in re.finditer(r"\n## ([^\n]+)\n(.*?)(?=\n## |\Z)", text, re.DOTALL):
        heading = m.group(1).strip()
        content = m.group(2).rstrip()
        # Strip trailing horizontal rule
        content = re.sub(r"\n---\s*$", "", content).strip()
        sections[heading] = content

    summary       = sections.get("Summary",        "")
    users_md      = sections.get("Users",           "")
    scope_md      = sections.get("Product Scope",   "")
    workflows_md  = sections.get("Core Workflows",  "")

    # Users: ### Name\nDescription
    users: list[UserPersona] = []
    for m in re.finditer(r"### ([^\n]+)\n(.*?)(?=\n### |\Z)", sections.get("Users", ""), re.DOTALL):
        users.append(UserPersona(name=m.group(1).strip(), description=m.group(2).strip()))

    # Product scope: ### Group\n- items
    scope_groups: list[ScopeGroup] = []
    for gm in re.finditer(r"### ([^\n]+)\n(.*?)(?=\n### |\Z)", sections.get("Product Scope", ""), re.DOTALL):
        items: list[ScopeItem] = []
        for line in gm.group(2).splitlines():
            stripped = line.strip()
            if not stripped.startswith("-"):
                continue
            stripped = stripped[1:].strip()
            # Handle sub-items (leading spaces in original)
            if stripped.startswith("-"):
                stripped = stripped[1:].strip()
            complete = "~~" in stripped or "🎆" in stripped
            text_clean = re.sub(r"~~(.+?)~~", r"\1", stripped).replace("🎆", "").strip()
            items.append(ScopeItem(text=text_clean, complete=complete))
        scope_groups.append(ScopeGroup(title=gm.group(1).strip(), items=items))

    workflows_md = sections.get("Core Workflows", "")

    # Features: ### N. Area > #### N.N SubArea > | table |
    wbs_areas: list[WBSArea] = []
    features_text = sections.get("Features", "")
    l1_pat = re.compile(r"^### (\d+)\. (.+)$", re.MULTILINE)
    l1_list = list(l1_pat.finditer(features_text))

    for i, l1 in enumerate(l1_list):
        area_num = int(l1.group(1))
        area_title = l1.group(2).strip()
        start = l1.end()
        end = l1_list[i + 1].start() if i + 1 < len(l1_list) else len(features_text)
        area_block = features_text[start:end]

        sub_areas: list[WBSSubArea] = []
        l2_pat = re.compile(r"^#### (\d+\.\d+) (.+)$", re.MULTILINE)
        l2_list = list(l2_pat.finditer(area_block))

        for j, l2 in enumerate(l2_list):
            sub_prefix = l2.group(1).strip()
            sub_title = l2.group(2).strip()
            sub_start = l2.end()
            sub_end = l2_list[j + 1].start() if j + 1 < len(l2_list) else len(area_block)
            sub_block = area_block[sub_start:sub_end]

            features: list[Feature] = []
            for row in sub_block.splitlines():
                row = row.strip()
                if not row.startswith("|") or not row.endswith("|"):
                    continue
                cells = [c.strip() for c in row.split("|")[1:-1]]
                if len(cells) < 3:
                    continue
                wbs_code, feat_name, status_str = cells[0], cells[1], cells[2]
                # Skip header and separator rows
                if not wbs_code or wbs_code.lower() == "wbs" or set(wbs_code) <= {"-", " "}:
                    continue
                try:
                    status = FeatureStatus(_normalize_status_str(status_str))
                except ValueError:
                    continue
                # 6-column format: WBS | Name | Status | Value | Effort | Notes
                # 7-column format: adds Flag
                # 8/9-column format: adds Owner / UAT Confirmed
                # 4-column format: WBS | Name | Status | Notes (legacy)
                if len(cells) >= 6:
                    value   = int(cells[3]) if cells[3].isdigit() else None
                    effort  = int(cells[4]) if cells[4].isdigit() else None
                    notes   = cells[5] if len(cells) > 5 else ""
                    flagged = len(cells) >= 7 and cells[6].strip().lower() in ("gap", "flagged", "true")
                    owner   = cells[7].strip() if len(cells) > 7 and cells[7].strip() else None
                    uat_confirmed = len(cells) > 8 and cells[8].strip().lower() in ("yes", "true")
                else:
                    value, effort = None, None
                    notes   = cells[3] if len(cells) > 3 else ""
                    flagged = False
                    owner = None
                    uat_confirmed = False
                features.append(Feature(wbs=wbs_code, name=feat_name, status=status,
                                        value=value, effort=effort, notes=notes,
                                        flagged=flagged, owner=owner, uat_confirmed=uat_confirmed))

            sub_areas.append(WBSSubArea(wbs_prefix=sub_prefix, title=sub_title, features=features))

        wbs_areas.append(WBSArea(number=area_num, title=area_title, sub_areas=sub_areas))

    gaps_md = sections.get("Known Gaps for Team Discussion", "")

    return ProductDoc(
        raw_text=text,
        title=title,
        summary=summary,
        users=users,
        users_md=users_md,
        scope_groups=scope_groups,
        scope_md=scope_md,
        workflows_md=workflows_md,
        wbs_areas=wbs_areas,
        gaps_md=gaps_md,
    )


def update_feature_status(path: Path, wbs: str, new_status: FeatureStatus) -> None:
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_feature_status(text, wbs, new_status))


def update_feature_name(path: Path, wbs: str, new_name: str) -> None:
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_feature_name(text, wbs, new_name))


def update_feature_notes(path: Path, wbs: str, new_notes: str) -> None:
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_feature_notes(text, wbs, new_notes))


def _replace_section(text: str, heading: str, new_content: str) -> str:
    """Replace the content of a ## section while preserving all other sections."""
    pattern = rf"(## {re.escape(heading)}\n)(.*?)(?=\n## |\Z)"
    body = new_content.strip() + "\n"
    result, n = re.subn(pattern, lambda m: m.group(1) + body + "\n", text, flags=re.DOTALL)
    if n == 0:
        raise ValueError(f"Section '## {heading}' not found")
    return result


def update_users(path: Path, new_markdown: str) -> None:
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_users(text, new_markdown))


def update_workflows(path: Path, new_markdown: str) -> None:
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_workflows(text, new_markdown))


def update_scope(path: Path, new_markdown: str) -> None:
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_scope(text, new_markdown))


def move_feature(path: Path, wbs: str, target_prefix: str) -> Feature:
    """Move a feature row from its current sub-area to target_prefix, assigning a new WBS code."""
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        new_text, feature = transform_move_feature(text, wbs, target_prefix)
        _atomic_write(path, new_text)
        return feature


def add_feature(path: Path, req: NewFeature) -> Feature:
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        new_text, feature = transform_add_feature(text, req)
        _atomic_write(path, new_text)
        return feature


# ── Pure transform functions (text-in / text-out, no I/O) ────────────────────

def transform_feature_status(text: str, wbs: str, new_status: FeatureStatus) -> str:
    pattern = rf"^(\| {re.escape(wbs)} \| [^|]+ \|)[ ]+{_STATUS_PAT}[ ]+(\| .*)$"
    new_text, n = re.subn(pattern, rf"\1 {new_status.value} \2", text, flags=re.MULTILINE)
    if n != 1:
        raise ValueError(f"Expected 1 match for WBS {wbs!r}, got {n}")
    return new_text


def transform_feature_name(text: str, wbs: str, new_name: str) -> str:
    pattern = re.compile(
        rf"^(\| {re.escape(wbs)} \| )[^|]+(\| {_STATUS_PAT} \| .*\|)$",
        re.MULTILINE,
    )
    new_text, n = pattern.subn(lambda m: f"{m.group(1)}{new_name} {m.group(2)}", text)
    if n != 1:
        raise ValueError(f"Expected 1 match for WBS {wbs!r}, got {n}")
    return new_text


def transform_feature_notes(text: str, wbs: str, new_notes: str) -> str:
    new_notes = new_notes.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
    # Match 6-column rows; any trailing columns (Flag/Owner/UAT Confirmed)
    # captured in group 3 and preserved as-is, however many there are.
    pattern = re.compile(
        rf"^(\| {re.escape(wbs)} \| [^|]+ \| {_STATUS_PAT} \|[^|]*\|[^|]*\|)([^|]*)(\|.*)$",
        re.MULTILINE,
    )
    new_text, n = pattern.subn(lambda m: f"{m.group(1)} {new_notes} {m.group(3)}", text)
    if n == 1:
        return new_text
    # Fallback: legacy 4-col or missing notes column
    pattern4 = re.compile(
        rf"^(\| {re.escape(wbs)} \| [^|]+ \| {_STATUS_PAT} \|)(?:[^|]*\|)?$",
        re.MULTILINE,
    )
    new_text, n = pattern4.subn(lambda m: f"{m.group(1)}  |  | {new_notes} |", text)
    if n != 1:
        raise ValueError(f"Expected 1 match for WBS {wbs!r}, got {n}")
    return new_text


def _normalize_feature_cells(cells: list[str]) -> list[str]:
    """Normalise a parsed feature row to at least 6 columns
    (WBS|Name|Status|Value|Effort|Notes), preserving any trailing
    Flag/Owner/UAT-Confirmed columns as-is."""
    if len(cells) == 4:
        return [cells[0], cells[1], cells[2], "", "", cells[3]]
    if len(cells) == 5:
        return [cells[0], cells[1], cells[2], cells[3], "", cells[4]]
    return list(cells)


def _set_feature_trailing_cell(text: str, wbs: str, index: int, value: str) -> str:
    """Set the trailing column at `index` (0-based; 6=Flag, 7=Owner, 8=UAT
    Confirmed) on a feature row, padding with empty columns as needed and
    preserving every other trailing column untouched. Drops the trailing
    columns entirely if none of them end up populated."""
    pattern = re.compile(
        rf"^\| {re.escape(wbs)} \| [^|]+ \| {_STATUS_PAT} \|.*\|$",
        re.MULTILINE,
    )

    def _replace(m: re.Match) -> str:
        cells = _normalize_feature_cells([c.strip() for c in m.group(0).split("|")[1:-1]])
        while len(cells) <= index:
            cells.append("")
        cells[index] = value
        if not any(cells[6:]):
            cells = cells[:6]
        return "| " + " | ".join(cells) + " |"

    new_text, n = pattern.subn(_replace, text)
    if n != 1:
        raise ValueError(f"Expected 1 match for WBS {wbs!r}, got {n}")
    return new_text


def transform_feature_flagged(text: str, wbs: str, flagged: bool) -> str:
    """Set or clear the gap flag (7th column) on a feature row."""
    return _set_feature_trailing_cell(text, wbs, 6, "gap" if flagged else "")


def transform_feature_owner(text: str, wbs: str, owner: str | None) -> str:
    """Set or clear the Owner (8th column) on a feature row."""
    return _set_feature_trailing_cell(text, wbs, 7, owner or "")


def transform_feature_uat(text: str, wbs: str, uat_confirmed: bool) -> str:
    """Set or clear the UAT Confirmed (9th column) on a feature row."""
    return _set_feature_trailing_cell(text, wbs, 8, "Yes" if uat_confirmed else "")


def get_feature_status(text: str, wbs: str) -> FeatureStatus | None:
    """Return the current status of a feature without a full parse."""
    m = re.search(
        rf"^\| {re.escape(wbs)} \| [^|]+ \| ({_STATUS_PAT}) \|",
        text, re.MULTILINE,
    )
    if not m:
        return None
    try:
        return FeatureStatus(_normalize_status_str(m.group(1).strip()))
    except ValueError:
        return None


def transform_feature_score(text: str, wbs: str, value: int | None, effort: int | None) -> str:
    """Update value and effort columns; pass None to clear a column.

    Normalises legacy 4-col rows to 6-col."""
    v_str = f" {value} " if value is not None else "  "
    e_str = f" {effort} " if effort is not None else " "
    # 6-column row; any trailing columns (Flag/Owner/UAT Confirmed) preserved
    # as-is in group 2, however many there are.
    pat6 = re.compile(
        rf"^(\| {re.escape(wbs)} \| [^|]+ \| {_STATUS_PAT} \|)[^|]*\|[^|]*(\|[^|]*\|.*)$",
        re.MULTILINE,
    )
    new_text, n = pat6.subn(lambda m: f"{m.group(1)}{v_str}|{e_str}{m.group(2)}", text)
    if n == 1:
        return new_text
    # Legacy 4-column row: expand to 6 columns by inserting value/effort before notes
    pat4 = re.compile(
        rf"^(\| {re.escape(wbs)} \| [^|]+ \| {_STATUS_PAT} \|)([^|]*)(\|)$",
        re.MULTILINE,
    )
    new_text, n = pat4.subn(lambda m: f"{m.group(1)}{v_str}|{e_str}|{m.group(2)}{m.group(3)}", text)
    if n != 1:
        raise ValueError(f"Expected 1 match for WBS {wbs!r}, got {n}")
    return new_text


def transform_users(text: str, new_markdown: str) -> str:
    return _replace_section(text, "Users", new_markdown)


def transform_workflows(text: str, new_markdown: str) -> str:
    return _replace_section(text, "Core Workflows", new_markdown)


def transform_scope(text: str, new_markdown: str) -> str:
    return _replace_section(text, "Product Scope", new_markdown)


def transform_add_feature(text: str, req: NewFeature) -> tuple[str, Feature]:
    sub_pattern = re.compile(rf"^#### {re.escape(req.wbs_prefix)} .+$", re.MULTILINE)
    sub_m = sub_pattern.search(text)
    if not sub_m:
        raise ValueError(f"Sub-area {req.wbs_prefix!r} not found")

    existing = re.findall(rf"^\| {re.escape(req.wbs_prefix)}\.(\d+) \|", text, re.MULTILINE)
    next_num = max((int(n) for n in existing), default=0) + 1
    new_wbs = f"{req.wbs_prefix}.{next_num}"

    after_header = text[sub_m.end():]
    next_header = re.search(r"\n(?:#{3,4}) ", after_header)
    sub_block = after_header[:next_header.start()] if next_header else after_header

    last_row_m = None
    for m in re.finditer(r"^\|.+\|$", sub_block, re.MULTILINE):
        last_row_m = m

    if last_row_m is None:
        raise ValueError(f"No table found in sub-area {req.wbs_prefix!r}")

    v_str = str(req.value) if req.value is not None else ""
    e_str = str(req.effort) if req.effort is not None else ""
    encoded_notes = req.notes.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
    new_row = f"| {new_wbs} | {req.name} | {req.status.value} | {v_str} | {e_str} | {encoded_notes} |"
    if req.owner:
        new_row += f"  | {req.owner} |"  # empty Flag column, then Owner
    insert_pos = sub_m.end() + last_row_m.end()
    new_text = text[:insert_pos] + "\n" + new_row + text[insert_pos:]
    return new_text, Feature(
        wbs=new_wbs, name=req.name, status=req.status, notes=req.notes,
        value=req.value, effort=req.effort, owner=req.owner,
    )


def transform_delete_feature(text: str, wbs: str) -> str:
    row_pat = re.compile(rf"^\| {re.escape(wbs)} \|[^\n]+\|$\n?", re.MULTILINE)
    new_text, n = row_pat.subn("", text)
    if n == 0:
        raise ValueError(f"Feature {wbs!r} not found")
    return re.sub(r"\n{3,}", "\n\n", new_text)


def delete_feature(path: Path, wbs: str) -> None:
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_delete_feature(text, wbs))


def transform_move_feature(text: str, wbs: str, target_prefix: str) -> tuple[str, Feature]:
    row_pat = re.compile(rf"^\| {re.escape(wbs)} \|[^\n]+\|$", re.MULTILINE)
    row_m = row_pat.search(text)
    if not row_m:
        raise ValueError(f"Feature {wbs!r} not found")
    row_text = row_m.group(0)

    cells = [c.strip() for c in row_text.split("|")[1:-1]]
    if len(cells) < 3:
        raise ValueError(f"Malformed row for {wbs!r}")
    feat_name  = cells[1]
    status_str = cells[2]
    try:
        status = FeatureStatus(_normalize_status_str(status_str))
    except ValueError:
        status = FeatureStatus.planned

    # Handle both 4-col (WBS|Name|Status|Notes) and 6-col (WBS|Name|Status|Value|Effort|Notes)
    if len(cells) >= 6:
        value  = int(cells[3]) if cells[3].isdigit() else None
        effort = int(cells[4]) if cells[4].isdigit() else None
        feat_notes = cells[5] if len(cells) > 5 else ""
    else:
        value, effort = None, None
        feat_notes = cells[3] if len(cells) > 3 else ""

    text = row_pat.sub("", text, count=1)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return transform_add_feature(text, NewFeature(
        wbs_prefix=target_prefix, name=feat_name, status=status,
        value=value, effort=effort, notes=feat_notes,
    ))
