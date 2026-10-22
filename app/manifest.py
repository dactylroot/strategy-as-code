"""Parse the Projects table of a program manifest (PROGRAM.MD).

The Projects table lists the member projects. The Initiatives section groups
features of several projects (`project:WBS` members) under program-level goals.
Kept free of app settings so it can be used
by the CLI and tests without importing config.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path

MODES = ("direct", "branch")
DEFAULT_PLANNING_BRANCH = "planning"


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ProgramInitiative:
    name: str
    kind: str                                  # "major" | "minor"
    members: tuple[tuple[str, str], ...] = ()  # (project key, WBS code)


@dataclass(frozen=True)
class ProjectSpec:
    key: str
    repo: str                      # clone URL, or an absolute local path
    role: str = ""
    depends_on: tuple[str, ...] = ()
    mode: str = "direct"           # direct: push the default branch; branch: push a planning branch
    branch: str = ""               # explicit target branch; "" = default branch (direct) or "planning" (branch)
    planning_dir: str = "."        # folder holding PRODUCT.MD/BUGS.MD/ABOUT.MD, relative to the repo root

    def planning_file(self, name: str) -> str:
        return str(Path(self.planning_dir) / name)


def _is_url(value: str) -> bool:
    return "://" in value or re.match(r"^[\w.-]+@[\w.-]+:", value) is not None


def _resolve_repo(value: str, base_dir: Path) -> str:
    if _is_url(value):
        return value
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = base_dir / p
    return str(p.resolve())


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _projects_block(text: str) -> list[str]:
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"^##\s+Projects\s*$", l)), None)
    if start is None:
        raise ManifestError("manifest has no '## Projects' section")
    block: list[str] = []
    for l in lines[start + 1:]:
        if l.startswith("## "):
            break
        if l.lstrip().startswith("|"):
            block.append(l)
    return block


def parse_manifest(text: str, base_dir: Path) -> list[ProjectSpec]:
    # Drop HTML comments first: the manifest keeps not-yet-onboarded rows in one.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    rows = _projects_block(text)
    if len(rows) < 2:
        raise ManifestError("Projects table has no header")
    header = [h.lower() for h in _split_row(rows[0])]
    col = {name: i for i, name in enumerate(header)}
    for needed in ("key", "repo / path"):
        if needed not in col:
            raise ManifestError(f"Projects table is missing the '{needed}' column")

    def cell(cells: list[str], name: str) -> str:
        i = col.get(name)
        return cells[i] if i is not None and i < len(cells) else ""

    specs: list[ProjectSpec] = []
    for line in rows[2:]:  # skip header and separator
        cells = _split_row(line)
        key = cell(cells, "key")
        if not key:
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", key):
            raise ManifestError(f"invalid project key {key!r} (lowercase letters, digits, . _ -)")
        repo = cell(cells, "repo / path")
        if not repo:
            raise ManifestError(f"project {key!r} has no repo or path")
        mode = (cell(cells, "mode") or "direct").lower()
        if mode not in MODES:
            raise ManifestError(f"project {key!r} has unknown mode {mode!r} (expected one of {MODES})")
        deps = tuple(d.strip() for d in cell(cells, "depends on").split(",") if d.strip())
        specs.append(ProjectSpec(
            key=key, repo=_resolve_repo(repo, base_dir), role=cell(cells, "role"),
            depends_on=deps, mode=mode, branch=cell(cells, "branch"),
            planning_dir=cell(cells, "planning dir") or ".",
        ))
    _validate(specs)
    return specs


def _validate(specs: list[ProjectSpec]) -> None:
    keys = [s.key for s in specs]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ManifestError(f"duplicate project keys: {sorted(dupes)}")
    known = set(keys)
    graph = {s.key: s.depends_on for s in specs}
    for s in specs:
        for d in s.depends_on:
            if d not in known:
                raise ManifestError(f"project {s.key!r} depends on unknown project {d!r}")
    # Depth-first cycle check.
    state: dict[str, int] = {}  # 1 = on stack, 2 = done

    def visit(k: str, path: list[str]) -> None:
        if state.get(k) == 2:
            return
        if state.get(k) == 1:
            raise ManifestError("dependency cycle: " + " -> ".join(path + [k]))
        state[k] = 1
        for d in graph[k]:
            visit(d, path + [k])
        state[k] = 2

    for k in keys:
        visit(k, [])


def load_manifest(path: Path) -> list[ProjectSpec]:
    path = Path(path)
    return parse_manifest(path.read_text(encoding="utf-8"), path.parent)


_MEMBER_RE = re.compile(r"^[-*]\s+`?([a-z0-9][a-z0-9._-]*):(\d+(?:\.\d+)*)`?\s*(?:[-–].*)?$")


def parse_initiatives(text: str, known_projects: set[str] | None = None) -> list[ProgramInitiative]:
    """Read `## Initiatives`: `### Name (Major|Minor)` headings with `- project:WBS` bullets.

    Bullets that are not `project:WBS` are ignored, so a description line can sit
    under a heading. A member naming a project outside `known_projects` is an error.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"^##\s+Initiatives\s*$", l)), None)
    if start is None:
        return []
    out: list[ProgramInitiative] = []
    name = kind = None
    members: list[tuple[str, str]] = []

    def flush() -> None:
        if name is not None:
            out.append(ProgramInitiative(name, kind, tuple(members)))

    for l in lines[start + 1:]:
        if l.startswith("## "):
            break
        h = re.match(r"^###\s+(.+?)\s*(?:\((major|minor)\))?\s*$", l, re.IGNORECASE)
        if h:
            flush()
            name, kind, members = h.group(1), (h.group(2) or "minor").lower(), []
            continue
        m = _MEMBER_RE.match(l.strip())
        if m and name is not None:
            if known_projects is not None and m.group(1) not in known_projects:
                raise ManifestError(f"initiative {name!r} names unknown project {m.group(1)!r}")
            members.append((m.group(1), m.group(2)))
    flush()
    return out


def load_initiatives(path: Path, known_projects: set[str] | None = None) -> list[ProgramInitiative]:
    return parse_initiatives(Path(path).read_text(encoding="utf-8"), known_projects)
