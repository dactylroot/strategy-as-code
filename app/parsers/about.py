from __future__ import annotations
import re
from pathlib import Path

from ..fileio import _atomic_write, _lock_for
from ..models import (
    AboutDoc, ChangelogEntry, ChangelogGroup,
    RoadmapSection, NewRelease, RoadmapUpdate, Initiative, InitiativeUpdate,
    FeatureStatus, ProductDoc,
)


def parse(path: Path) -> AboutDoc:
    text = path.read_text(encoding="utf-8")
    return _parse_text(text)


def _parse_text(text: str) -> AboutDoc:
    changelog: list[ChangelogEntry] = []
    roadmap: list[RoadmapSection] = []

    # Split on top-level # headings
    top_sections: dict[str, str] = {}
    for m in re.finditer(r"\n# ([^\n]+)\n(.*?)(?=\n# |\Z)", "\n" + text, re.DOTALL):
        top_sections[m.group(1).strip()] = m.group(2)

    # Parse changelog entries
    changelog_text = top_sections.get("Changelog", "")
    for em in re.finditer(r"\n## ([^\n]+)\n(.*?)(?=\n## |\Z)", "\n" + changelog_text, re.DOTALL):
        version_header = em.group(1).strip()
        in_progress = "(in progress)" in version_header.lower()
        version = re.sub(r"\s*\(.*?\)", "", version_header).strip()
        entry_body = em.group(2)

        groups: list[ChangelogGroup] = []
        bug_fixes: list[str] = []
        current_label: str | None = None
        current_items: list[str] = []

        for line in entry_body.splitlines():
            bold_m = re.match(r"^\*\*(.+?)\*\*\s*$", line.strip())
            bullet_m = re.match(r"^[-*]\s+(.+)", line.strip())

            if bold_m:
                # Save previous group
                if current_label is not None:
                    if current_label.lower() == "bug fixes":
                        bug_fixes.extend(current_items)
                    else:
                        groups.append(ChangelogGroup(label=current_label, items=current_items))
                current_label = bold_m.group(1).strip()
                current_items = []
            elif bullet_m and current_label is not None:
                current_items.append(bullet_m.group(1).strip())

        # Flush last group
        if current_label is not None:
            if current_label.lower() == "bug fixes":
                bug_fixes.extend(current_items)
            else:
                groups.append(ChangelogGroup(label=current_label, items=current_items))

        changelog.append(ChangelogEntry(
            version=version,
            in_progress=in_progress,
            groups=groups,
            bug_fixes=bug_fixes,
        ))

    # Parse roadmap sections
    roadmap_text = top_sections.get("Roadmap", "")
    initiatives: list[Initiative] = []
    # Skip any preamble before first ## heading
    for sm in re.finditer(r"\n## ([^\n]+)\n(.*?)(?=\n## |\Z)", "\n" + roadmap_text, re.DOTALL):
        name = sm.group(1).strip()
        body = sm.group(2)
        if name == "Initiatives":
            initiatives = _parse_initiatives(body)
        else:
            items = [
                m.group(1).strip()
                for m in re.finditer(r"^[-*]\s+(.+)", body, re.MULTILINE)
            ]
            # For "1.0.0" section the body is prose, not bullets
            if not items:
                plain = body.strip()
                if plain:
                    items = [plain]
            roadmap.append(RoadmapSection(name=name, items=items))

    return AboutDoc(raw_text=text, changelog=changelog, roadmap=roadmap, initiatives=initiatives)


def update_roadmap(path: Path, update: RoadmapUpdate) -> None:
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_update_roadmap(text, update))


def add_changelog_entry(path: Path, release: NewRelease, in_progress_items: list[str]) -> None:
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding="utf-8")
        _atomic_write(path, transform_add_changelog_entry(text, release, in_progress_items))


# ── Pure transform functions (text-in / text-out, no I/O) ────────────────────

_INITIATIVE_HEADING_RE = re.compile(r"^(.*?)\s*\((Major|Minor)\)\s*$")


def _parse_initiatives(body: str) -> list[Initiative]:
    """Parse the Initiatives section body: one ### heading per initiative,
    tagged (Major) or (Minor), with WBS feature codes as bullet items."""
    parts = re.split(r"^### ([^\n]+)\n", body, flags=re.MULTILINE)
    # parts[0] is any preamble before the first heading - ignored, every
    # initiative must be named.
    initiatives: list[Initiative] = []
    for i in range(1, len(parts) - 1, 2):
        heading = parts[i].strip()
        m = _INITIATIVE_HEADING_RE.match(heading)
        name = m.group(1).strip() if m else heading
        kind = m.group(2).lower() if m else "minor"
        ini_body = parts[i + 1]
        items = [mm.group(1).strip() for mm in re.finditer(r"^[-*]\s+(.+)", ini_body, re.MULTILINE)]
        initiatives.append(Initiative(name=name, kind=kind, items=items))
    return initiatives


def _format_initiatives(initiatives: list[Initiative] | list[InitiativeUpdate]) -> str:
    lines: list[str] = []
    for ini in initiatives:
        label = "Major" if ini.kind == "major" else "Minor"
        items = ini.items if hasattr(ini, "items") else ini.wbs
        lines.append(f"### {ini.name} ({label})")
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    return "\n".join(lines)


def _replace_roadmap_section(src: str, section_name: str, new_items: list[str]) -> str:
    body = "\n".join(f"- {item}" for item in new_items) if new_items else ""
    pattern = rf"(## {re.escape(section_name)}\n)(.*?)(?=\n## |\n# |\Z)"
    replacement = rf"\g<1>{body}\n"
    result, n = re.subn(pattern, replacement, src, flags=re.DOTALL)
    if n == 0:
        return src
    return result


def transform_update_roadmap(text: str, update: RoadmapUpdate) -> str:
    return _replace_roadmap_section(text, "Backlog", update.backlog)


def transform_update_initiatives(text: str, initiatives: list[InitiativeUpdate]) -> str:
    content = _format_initiatives(initiatives)
    pattern = rf"(## {re.escape('Initiatives')}\n)(.*?)(?=\n## |\n# |\Z)"
    result, n = re.subn(pattern, rf"\g<1>{content}", text, flags=re.DOTALL)
    if n == 0:
        # No existing "## Initiatives" heading - insert one before "## Backlog",
        # or at the end of the Roadmap section if there's no Backlog either.
        insertion = f"## Initiatives\n\n{content}"
        pattern = r"(\n## Backlog\n)"
        result, n = re.subn(pattern, f"\n{insertion}\\1", text, count=1)
        if n == 0:
            result = text.rstrip("\n") + f"\n\n{insertion}".rstrip("\n") + "\n"
    return result


def transform_clear_completed_initiatives(text: str, product: ProductDoc) -> str:
    """Remove initiatives whose every listed feature is Live/Released."""
    about = _parse_text(text)
    if not about.initiatives:
        return text
    all_features = {
        f.wbs: f
        for area in product.wbs_areas
        for sa in area.sub_areas
        for f in sa.features
    }
    done = {FeatureStatus.live, FeatureStatus.released}
    remaining = [
        ini for ini in about.initiatives
        if not (ini.items and all(all_features.get(w) is not None and all_features[w].status in done for w in ini.items))
    ]
    if len(remaining) == len(about.initiatives):
        return text
    return transform_update_initiatives(text, [
        InitiativeUpdate(name=ini.name, kind=ini.kind, wbs=ini.items) for ini in remaining
    ])


def transform_add_changelog_entry(text: str, release: NewRelease, in_progress_items: list[str]) -> str:
    lines = [f"## {release.version}", ""]
    for label in in_progress_items:
        lines.append(f"**{label}**")
        lines.append("")
    if release.bug_fixes:
        lines.append("**Bug fixes**")
        for fix in release.bug_fixes:
            lines.append(f"- {fix}")
        lines.append("")

    entry_text = "\n".join(lines) + "\n"

    changelog_pos = text.find("# Changelog\n")
    if changelog_pos == -1:
        raise ValueError("# Changelog heading not found in ABOUT.MD")

    insert_at = changelog_pos + len("# Changelog\n") + 1
    new_text = text[:insert_at] + entry_text + "\n" + text[insert_at:]
    return new_text
