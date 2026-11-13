"""Parse the Projects table of a program manifest (PROGRAM.MD).

The Projects table lists the member projects. The Initiatives section groups
features of several projects (`project:WBS` members) under program-level goals.
Kept free of app settings so it can be used
by the CLI and tests without importing config.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, replace
from pathlib import Path

MODES = ("direct", "branch")
KINDS = ("project", "program")
MAX_DEPTH = 3                      # how many levels a walk across dependent projects may go
DEFAULT_PLANNING_BRANCH = "planning"


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ProgramInitiative:
    name: str
    kind: str                                  # "major" | "minor"
    members: tuple[tuple[str, str], ...] = ()  # (project key, WBS code)
    aliases: tuple[str, ...] = ()              # other names that mean this initiative (`Aliases:` line)
    excludes: tuple[str, ...] = ()             # projects whose same-named initiative is a different one (`Not:` line)
    draft: bool = False                        # `(Major, Draft)`: being planned; leaves Drafts once a project defines work for it
    goal: str = ""                             # `Goal:` line
    success: str = ""                          # `Success:` line
    asks: tuple[tuple[str, str], ...] = ()     # `Ask <project>: hint` lines: (project, hint) each project is asked to plan


def normalize_name(name: str) -> str:
    """The key under which initiatives with the same name are one initiative: case, spacing and
    punctuation at the edges do not matter ("Faster  checkout" is "Faster Checkout")."""
    return re.sub(r"\s+", " ", name).strip(" .:;-").casefold()


@dataclass(frozen=True)
class ProjectSpec:
    key: str
    repo: str                      # clone URL, or an absolute local path
    role: str = ""
    depends_on: tuple[str, ...] = ()
    mode: str = "direct"           # direct: push the default branch; branch: push a planning branch
    branch: str = ""               # explicit target branch; "" = default branch (direct) or "planning" (branch)
    planning_dir: str = "."        # folder holding PRODUCT.MD/BUGS.MD/ABOUT.MD, relative to the repo root
    kind: str = "project"          # "program": a nested program; its own PROGRAM.MD lists further projects
    parent: str = ""               # key of the nested program this project was listed in
    app_url: str = ""              # the project's own web UI; where a person without program access goes back to

    def planning_file(self, name: str) -> str:
        return str(Path(self.planning_dir) / name)


def repo_identity(repo: str, planning_dir: str = ".") -> str:
    """What makes two manifest rows the same planning location: the repository (owner/name for a
    GitHub URL, the resolved path for a local one) and the folder inside it. Used to stop a nested
    program from listing itself, directly or through another program."""
    m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", repo) if _is_url(repo) else None
    where = m.group(1).lower() if m else str(Path(repo).resolve()) if not _is_url(repo) else repo.lower()
    return f"{where}#{str(Path(planning_dir or '.'))}"


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
        kind = (cell(cells, "kind") or "project").lower()
        if kind not in KINDS:
            raise ManifestError(f"project {key!r} has unknown kind {kind!r} (expected one of {KINDS})")
        specs.append(ProjectSpec(
            key=key, repo=_resolve_repo(repo, base_dir), role=cell(cells, "role"),
            depends_on=deps, mode=mode, branch=cell(cells, "branch"),
            planning_dir=cell(cells, "planning dir") or ".", kind=kind, app_url=cell(cells, "app url"),
        ))
    _validate(specs, allow_nested=True)
    return specs


def nest(specs: list[ProjectSpec], parent: str) -> list[ProjectSpec]:
    """The projects listed by nested program `parent`, with keys and dependencies prefixed
    `<parent>.` so they cannot collide with the outer program's own keys."""
    return [replace(s, key=f"{parent}.{s.key}", parent=parent,
                    depends_on=tuple(f"{parent}.{d}" for d in s.depends_on)) for s in specs]


def validate(specs: list[ProjectSpec]) -> None:
    """Check a complete, flattened project list (nested programs already expanded)."""
    _validate(specs, allow_nested=False)


def _validate(specs: list[ProjectSpec], allow_nested: bool) -> None:
    keys = [s.key for s in specs]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ManifestError(f"duplicate project keys: {sorted(dupes)}")
    known = set(keys)
    programs = {s.key for s in specs if s.kind == "program"}
    graph = {s.key: s.depends_on for s in specs}
    for s in specs:
        for d in s.depends_on:
            # Before expansion, `<program>.<project>` names a project the nested program will supply.
            if d not in known and not (allow_nested and d.split(".", 1)[0] in programs and "." in d):
                raise ManifestError(f"project {s.key!r} depends on unknown project {d!r}")
    # Depth-first cycle check.
    state: dict[str, int] = {}  # 1 = on stack, 2 = done

    def visit(k: str, path: list[str]) -> None:
        if k not in graph or state.get(k) == 2:
            return
        if state.get(k) == 1:
            raise ManifestError("dependency cycle: " + " -> ".join(path + [k]))
        state[k] = 1
        for d in graph[k]:
            visit(d, path + [k])
        state[k] = 2

    for k in keys:
        visit(k, [])


@dataclass(frozen=True)
class Visit:
    key: str
    depth: int                     # 0 for the starting project
    via: str = ""                  # the project this one was reached from
    repeat: bool = False           # already visited on another branch of the walk: not expanded again
    truncated: bool = False        # has further projects beyond the depth cap: not expanded


def walk(specs: list[ProjectSpec], start: str, direction: str = "down", max_depth: int = MAX_DEPTH) -> list[Visit]:
    """Depth-first walk of the dependency graph from `start`, in visiting order.

    "down" follows `Depends On` (what the project needs); "up" follows the reverse
    (what needs it). A project reached twice is listed again but marked `repeat` and
    not expanded, so shared dependencies and cycles terminate. Nothing is expanded
    below `max_depth` levels, and `max_depth` itself cannot exceed `MAX_DEPTH`.
    """
    if direction not in ("down", "up"):
        raise ManifestError("direction must be 'down' or 'up'")
    if not 0 <= max_depth <= MAX_DEPTH:
        raise ManifestError(f"depth must be between 0 and {MAX_DEPTH}")
    by_key = {s.key: s for s in specs}
    if start not in by_key:
        raise ManifestError(f"unknown project {start!r}")
    nexts = {k: list(s.depends_on) for k, s in by_key.items()} if direction == "down" else {
        k: [o.key for o in specs if k in o.depends_on] for k in by_key}
    seen: set[str] = set()
    out: list[Visit] = []

    def visit(key: str, depth: int, via: str) -> None:
        if key in seen:
            out.append(Visit(key, depth, via, repeat=True))
            return
        seen.add(key)
        children = [c for c in nexts[key] if c in by_key]
        if children and depth >= max_depth:
            out.append(Visit(key, depth, via, truncated=True))
            return
        out.append(Visit(key, depth, via))
        for child in children:
            visit(child, depth + 1, key)

    visit(start, 0, "")
    return out


def load_manifest(path: Path) -> list[ProjectSpec]:
    path = Path(path)
    return parse_manifest(path.read_text(encoding="utf-8"), path.parent)


_FIELD_RE = re.compile(r"^(aliases|not|goal|success)\s*:\s*(.*)$", re.IGNORECASE)
_ASK_RE = re.compile(r"^ask\s+([a-z0-9][a-z0-9._-]*)\s*:\s*(.*)$", re.IGNORECASE)
_TAG_RE = re.compile(r"^(.+?)\s*\(([^()]*)\)$")


def parse_heading(text: str) -> tuple[str, str, bool]:
    """`Name (Major, Draft)` -> (name, kind, draft). A trailing parenthesis that is not made only of
    Major, Minor and Draft is part of the name. No tag means Minor."""
    text = text.strip()
    m = _TAG_RE.match(text)
    if m:
        tokens = [t.strip().lower() for t in m.group(2).split(",")]
        if all(t in ("major", "minor", "draft") for t in tokens):
            return m.group(1).strip(), "major" if "major" in tokens else "minor", "draft" in tokens
    return text, "minor", False


def _csv(value: str) -> list[str]:
    return [v.strip().strip("`") for v in value.split(",") if v.strip().strip("`")]


_MEMBER_RE = re.compile(r"^[-*]\s+`?([a-z0-9][a-z0-9._-]*):(\d+(?:\.\d+)*)`?\s*(?:[-–].*)?$")


def parse_initiatives(text: str, known_projects: set[str] | None = None) -> list[ProgramInitiative]:
    """Read `## Initiatives`: `### Name (Major|Minor)` headings with `- project:WBS` bullets.

    Bullets that are not `project:WBS` are ignored, so a description line can sit
    under a heading. `(Major, Draft)` marks an initiative still being planned; `Goal:`, `Success:`
    and `Ask <project>: hint` lines describe it and name the projects asked to plan their part. Two optional lines refine how same-named project initiatives are
    folded in: `Aliases: A, B` (those names mean this initiative) and `Not: project, ...`
    (that project's same-named initiative is a different one). A member naming a project outside `known_projects` is an error.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"^##\s+Initiatives\s*$", l)), None)
    if start is None:
        return []
    out: list[ProgramInitiative] = []
    name = kind = None
    draft = False
    members: list[tuple[str, str]] = []
    aliases: list[str] = []
    excludes: list[str] = []
    asks: list[tuple[str, str]] = []
    texts = {"goal": "", "success": ""}

    def check(proj: str) -> None:
        if known_projects is not None and proj not in known_projects:
            raise ManifestError(f"initiative {name!r} names unknown project {proj!r}")

    def flush() -> None:
        if name is not None:
            out.append(ProgramInitiative(name, kind, tuple(members), tuple(aliases), tuple(excludes), draft,
                                         texts["goal"], texts["success"], tuple(asks)))

    for l in lines[start + 1:]:
        if l.startswith("## "):
            break
        h = re.match(r"^###\s+(.+?)\s*$", l)
        if h:
            flush()
            name, kind, draft = parse_heading(h.group(1))
            members, aliases, excludes, asks, texts = [], [], [], [], {"goal": "", "success": ""}
            continue
        if name is None:
            continue
        line = l.strip()
        f = _FIELD_RE.match(line)
        if f:
            field_name = f.group(1).lower()
            if field_name == "aliases":
                aliases.extend(_csv(f.group(2)))
            elif field_name == "not":
                for proj in _csv(f.group(2)):
                    if known_projects is not None and proj not in known_projects:
                        raise ManifestError(f"initiative {name!r} excludes unknown project {proj!r}")
                    excludes.append(proj)
            else:
                texts[field_name] = f.group(2).strip()
            continue
        a_ = _ASK_RE.match(line)
        if a_:
            check(a_.group(1))
            asks.append((a_.group(1), a_.group(2).strip()))
            continue
        m = _MEMBER_RE.match(line)
        if m:
            check(m.group(1))
            members.append((m.group(1), m.group(2)))
    flush()
    return out


def load_initiatives(path: Path, known_projects: set[str] | None = None) -> list[ProgramInitiative]:
    return parse_initiatives(Path(path).read_text(encoding="utf-8"), known_projects)


# ── Text transforms: change one initiative's block without touching the rest of the manifest ──
# Text in, text out, like the transforms in parsers/. They only ever edit lines inside the
# `## Initiatives` section (creating it after the Projects table if it is missing), so a
# writer built on them cannot alter which repositories the service clones. HTML-commented
# blocks are skipped when looking for a heading, as `parse_initiatives` skips them.

_HEAD_LINE_RE = re.compile(r"^###\s+(.+?)\s*$")
_WBS_RE = re.compile(r"\d+(?:\.\d+)*")
_KEY_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_FIELD_ORDER = ("goal", "success", "aliases", "not", "ask")


def _masked(text: str) -> list[str]:
    """The lines with HTML comments blanked out (same line count), for finding structure."""
    return re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.DOTALL).split("\n")


def _section_end(masked: list[str], start: int) -> int:
    return next((i for i in range(start + 1, len(masked)) if masked[i].startswith("## ")), len(masked))


def _blocks(masked: list[str]) -> tuple[int | None, int, list[tuple[int, int, str]]]:
    """(section heading line or None, section end, [(heading line, block end, name)])."""
    start = next((i for i, l in enumerate(masked) if re.match(r"^##\s+Initiatives\s*$", l)), None)
    if start is None:
        return None, len(masked), []
    end = _section_end(masked, start)
    heads = [i for i in range(start + 1, end) if _HEAD_LINE_RE.match(masked[i])]
    out = []
    for n, h in enumerate(heads):
        out.append((h, heads[n + 1] if n + 1 < len(heads) else end, parse_heading(_HEAD_LINE_RE.match(masked[h]).group(1))[0]))
    return start, end, out


def _find(masked: list[str], name: str) -> tuple[int, int, str] | None:
    want = normalize_name(name)
    return next((b for b in _blocks(masked)[2] if normalize_name(b[2]) == want), None)


def _last_content(lines: list[str], lo: int, hi: int) -> int:
    """Index of the last non-blank line in [lo, hi), or lo - 1 when there is none."""
    return next((i for i in range(hi - 1, lo - 1, -1) if lines[i].strip()), lo - 1)


def _check_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name or "<!--" in name or "-->" in name or parse_heading(name)[0] != name:
        raise ManifestError(f"invalid initiative name {name!r}")
    return name


def _check_kind(kind: str) -> str:
    if kind.lower() not in ("major", "minor"):
        raise ManifestError(f"initiative kind must be Major or Minor, not {kind!r}")
    return kind.lower()


def _check_members(members) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for project, wbs in members:
        if not _KEY_RE.fullmatch(project) or not _WBS_RE.fullmatch(wbs):
            raise ManifestError(f"invalid initiative member {project}:{wbs}")
        if (project, wbs) not in out:
            out.append((project, wbs))
    return out


def _check_list(values, what: str) -> list[str]:
    out = [re.sub(r"\s+", " ", v).strip() for v in values]
    if any(not v or "," in v or "<!--" in v for v in out):
        raise ManifestError(f"invalid {what}")
    return list(dict.fromkeys(out))


def _check_text(value: str, what: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    if "<!--" in value or "-->" in value:
        raise ManifestError(f"invalid {what}")
    return value


def _check_asks(asks) -> list[tuple[str, str]]:
    out: dict[str, str] = {}
    for project, text in asks:
        if not _KEY_RE.fullmatch(project):
            raise ManifestError(f"invalid project in an ask: {project!r}")
        out.setdefault(project, _check_text(text, "ask"))
    return list(out.items())


def _field_lines(goal=None, success=None, aliases=None, excludes=None, asks=None) -> dict[str, list[str]]:
    """The manifest lines for each field that was given (None = not given); an empty value gives no line."""
    out: dict[str, list[str]] = {}
    if goal is not None:
        g = _check_text(goal, "goal")
        out["goal"] = [f"Goal: {g}"] if g else []
    if success is not None:
        v = _check_text(success, "success criteria")
        out["success"] = [f"Success: {v}"] if v else []
    if aliases is not None:
        v = _check_list(aliases, "alias")
        out["aliases"] = ["Aliases: " + ", ".join(v)] if v else []
    if excludes is not None:
        v = _check_list(excludes, "project in Not:")
        out["not"] = ["Not: " + ", ".join(v)] if v else []
    if asks is not None:
        out["ask"] = [f"Ask {p}: {t}".rstrip() if t else f"Ask {p}:" for p, t in _check_asks(asks)]
    return out


def _heading(name: str, kind: str, draft: bool) -> str:
    return f"### {name} ({kind.title()}{', Draft' if draft else ''})"


def _line_field(masked_line: str) -> str | None:
    line = masked_line.strip()
    f = _FIELD_RE.match(line)
    if f:
        return f.group(1).lower()
    return "ask" if _ASK_RE.match(line) else None


def transform_add_initiative(text: str, name: str, kind: str = "minor", members=(), aliases=(), excludes=(),
                             draft: bool = False, goal: str = "", success: str = "", asks=()) -> str:
    name, kind = _check_name(name), _check_kind(kind)
    members = _check_members(members)
    fields = _field_lines(goal, success, aliases, excludes, asks)
    lines, masked = text.split("\n"), _masked(text)
    if _find(masked, name):
        raise ManifestError(f"initiative {name!r} already exists")
    block = [_heading(name, kind, draft), *(l for f in _FIELD_ORDER for l in fields.get(f, [])),
             *(f"- {p}:{w}" for p, w in members)]
    start, end, _ = _blocks(masked)
    if start is not None:
        last = _last_content(lines, start + 1, end)
        lines[last + 1:last + 1] = ["", *block] if last > start else block
    else:
        proj = next((i for i, l in enumerate(masked) if re.match(r"^##\s+Projects\s*$", l)), None)
        at = _section_end(masked, proj) if proj is not None else len(lines)
        section = ["## Initiatives", "", *block, ""]
        if at >= len(lines):                      # no later section: append after the last content
            at = _last_content(lines, 0, len(lines)) + 1
            lines[at:] = ["", *section]
        else:
            lines[at:at] = section
    return "\n".join(lines)


def transform_update_initiative(text: str, name: str, kind: str | None = None, members=None, aliases=None,
                                excludes=None, draft: bool | None = None, goal: str | None = None,
                                success: str | None = None, asks=None) -> str:
    """Change the given parts of an initiative; None leaves a part as it is. Lines the app does not
    own (a description under the heading, comments) stay where they are."""
    lines, masked = text.split("\n"), _masked(text)
    hit = _find(masked, name)
    if hit is None:
        raise ManifestError(f"no initiative named {name!r}")
    h, end, current = hit
    block, mblock = lines[h:end], masked[h:end]
    if kind is not None or draft is not None:
        _, kind0, draft0 = parse_heading(_HEAD_LINE_RE.match(mblock[0]).group(1))
        block[0] = _heading(current, _check_kind(kind) if kind is not None else kind0, draft0 if draft is None else draft)
    changed = _field_lines(goal, success, aliases, excludes, asks)
    if changed:
        keep = [i for i in range(len(block)) if i == 0 or _line_field(mblock[i]) not in changed]
        block, mblock = [block[i] for i in keep], [mblock[i] for i in keep]
        block[1:1] = [l for f in _FIELD_ORDER for l in changed.get(f, [])]
        mblock[1:1] = block[1:1 + sum(len(v) for v in changed.values())]
    if members is not None:
        want = _check_members(members)
        have = {}
        for i, l in enumerate(mblock):
            m = _MEMBER_RE.match(l.strip())
            if m and i > 0:
                have[i] = (m.group(1), m.group(2))
        drop = {i for i, pw in have.items() if pw not in want}
        kept = [i - sum(d < i for d in drop) for i in sorted(have) if i not in drop]
        block = [l for i, l in enumerate(block) if i not in drop]
        add = [f"- {p}:{w}" for p, w in want if (p, w) not in have.values()]
        at = (kept[-1] if kept else _last_content(block, 1, len(block))) + 1
        block[at:at] = add
    lines[h:end] = block
    return "\n".join(lines)


def transform_remove_initiative(text: str, name: str) -> str:
    lines, masked = text.split("\n"), _masked(text)
    hit = _find(masked, name)
    if hit is None:
        raise ManifestError(f"no initiative named {name!r}")
    h, end, _ = hit
    del lines[h:end]
    return "\n".join(lines)
