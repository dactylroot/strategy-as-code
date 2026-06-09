from __future__ import annotations
import os
import re
import threading
from pathlib import Path

from ..models import (
    AboutDoc, ChangelogEntry, ChangelogGroup,
    RoadmapSection, NewRelease, RoadmapUpdate,
)

_locks: dict[Path, threading.Lock] = {}
_locks_mutex = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _locks_mutex:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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
    # Skip any preamble before first ## heading
    for sm in re.finditer(r"\n## ([^\n]+)\n(.*?)(?=\n## |\Z)", "\n" + roadmap_text, re.DOTALL):
        name = sm.group(1).strip()
        items = [
            m.group(1).strip()
            for m in re.finditer(r"^[-*]\s+(.+)", sm.group(2), re.MULTILINE)
        ]
        # For "1.0.0" section the body is prose, not bullets
        if not items:
            body = sm.group(2).strip()
            if body:
                items = [body]
        roadmap.append(RoadmapSection(name=name, items=items))

    return AboutDoc(raw_text=text, changelog=changelog, roadmap=roadmap)


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

def _replace_roadmap_section(src: str, section_name: str, new_items: list[str]) -> str:
    body = "\n".join(f"- {item}" for item in new_items) if new_items else ""
    pattern = rf"(## {re.escape(section_name)}\n)(.*?)(?=\n## |\n# |\Z)"
    replacement = rf"\g<1>{body}\n"
    result, n = re.subn(pattern, replacement, src, flags=re.DOTALL)
    if n == 0:
        return src
    return result


def transform_update_roadmap(text: str, update: RoadmapUpdate) -> str:
    text = _replace_roadmap_section(text, "In Progress", update.in_progress)
    text = _replace_roadmap_section(text, "Planned", update.planned)
    text = _replace_roadmap_section(text, "Backlog", update.backlog)
    return text


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

    pattern = rf"(## {re.escape('In Progress')}\n)(.*?)(?=\n## |\n# |\Z)"
    new_text, _ = re.subn(pattern, rf"\g<1>", new_text, flags=re.DOTALL)
    return new_text
