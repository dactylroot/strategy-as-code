"""Program overview: one summary row per member project.

Reads each project's planning files from its managed clone, refreshing the
clones concurrently. A project that cannot be read yields an error row; it never
hides the others.
"""
from __future__ import annotations
import copy
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import inbox as inbox_lib
from . import scope
from .models import BugSeverity, BugStatus, FeatureStatus
from .parsers import about as about_parser
from .parsers import bugs as bugs_parser
from .parsers import product as product_parser


@dataclass
class ProjectSummary:
    key: str
    role: str = ""
    depends_on: tuple[str, ...] = ()
    required_by: tuple[str, ...] = ()
    mode: str = "direct"
    pr_url: str = ""
    parent: str = ""
    branch: str = ""
    sha: str = ""
    stale: bool = False
    error: str | None = None
    title: str = ""
    features: dict[str, int] = field(default_factory=dict)   # stage -> count
    total: int = 0
    done_pct: int = 0                                         # Live + Released
    in_progress: list[tuple[str, str, str]] = field(default_factory=list)  # (wbs, name, owner)
    awaiting_uat: int = 0                                     # Live, not yet Released
    flagged: int = 0
    bugs: dict[str, int] = field(default_factory=dict)       # severity -> active count
    bug_total: int = 0
    initiatives: list[tuple[str, str, int, int]] = field(default_factory=list)  # name, kind, done, total
    version: str = ""
    bug_status: dict[str, int] = field(default_factory=dict)  # Open / Investigating / Resolved
    last_fetch_age: int | None = None    # seconds since the last successful fetch
    last_push_age: int | None = None
    sync_error: str | None = None
    pushes: int = 0
    blocked: list[str] = field(default_factory=list)   # "project:WBS waits on dep:WBS"
    inbox_count: int = 0
    inbox_oldest_days: int = 0
    inbox_stale: int = 0
    stages_by_wbs: dict[str, tuple[str, str]] = field(default_factory=dict)  # wbs -> (name, stage)


@dataclass
class ProgramInitiativeView:
    name: str
    kind: str
    done: int = 0
    total: int = 0
    members: list[dict] = field(default_factory=list)   # project, wbs, name, stage, done, missing, blocked_by


def _load_content(s: ProjectSummary, root) -> None:
    """Parse the project's planning files into `s`. Pure function of the files."""
    product = product_parser._parse_text((root / "PRODUCT.MD").read_text(encoding="utf-8"))
    s.title = product.title
    feats = product.all_features
    s.total = len(feats)
    s.stages_by_wbs = {f.wbs: (f.name, "In-Progress" if f.status == FeatureStatus.planned else
                               ("Idea" if f.stage == "Gap" else f.stage)) for f in feats}
    s.features = {st: 0 for st in STAGES}
    for f in feats:
        stage = "In-Progress" if f.status == FeatureStatus.planned else f.stage
        if stage == "Gap":
            stage = "Idea"
        s.features[stage] = s.features.get(stage, 0) + 1
    done = s.features["Live"] + s.features["Released"]
    s.done_pct = round(100 * done / s.total) if s.total else 0
    s.awaiting_uat = s.features["Live"]
    s.flagged = sum(1 for f in feats if f.flagged)
    s.in_progress = [(f.wbs, f.name, f.owner or "") for f in feats
                     if f.status in (FeatureStatus.in_progress, FeatureStatus.planned)]

    bfile = root / "BUGS.MD"
    if bfile.exists():
        doc = bugs_parser._parse_text(bfile.read_text(encoding="utf-8"))
        s.bug_total = len(doc.active)
        s.bugs = {sev.value: sum(1 for b in doc.active if b.severity == sev) for sev in BugSeverity}
        s.bug_status = {st.value: sum(1 for b in doc.active if b.status == st) for st in BugStatus}

    afile = root / "ABOUT.MD"
    if afile.exists():
        about = about_parser._parse_text(afile.read_text(encoding="utf-8"))
        by_wbs = {f.wbs: f for f in feats}
        for ini in about.initiatives:
            members = [by_wbs[w] for w in ini.items if w in by_wbs]
            n_done = sum(1 for f in members if f.status in (FeatureStatus.live, FeatureStatus.released))
            s.initiatives.append((ini.name, ini.kind, n_done, len(members)))
        if about.changelog:
            s.version = about.changelog[0].version


_CONTENT_FIELDS = ("title", "features", "total", "done_pct", "in_progress", "awaiting_uat", "flagged",
                   "bugs", "bug_total", "bug_status", "initiatives", "version", "stages_by_wbs")
_cache: dict[tuple[str, str], dict] = {}


def _fill_content(s: ProjectSummary, project, root, sha: str | None) -> None:
    """Fill the file-derived fields, re-parsing only when the project's commit changed (2.1.4)."""
    cache_key = (s.key, sha or "")
    hit = _cache.get(cache_key) if sha else None
    if hit is None:
        _load_content(s, root)
        if sha:
            for k in [k for k in _cache if k[0] == s.key]:
                del _cache[k]
            _cache[cache_key] = {f: copy.deepcopy(getattr(s, f)) for f in _CONTENT_FIELDS}
        return
    for f in _CONTENT_FIELDS:
        setattr(s, f, copy.deepcopy(hit[f]))


STAGES = ("Idea", "Scored", "In-Progress", "Live", "Released")


def _summarize(key: str) -> ProjectSummary:
    reg = scope.get_registry()
    project = reg.get(key)
    spec = project.spec
    s = ProjectSummary(
        key=key, role=spec.role, depends_on=spec.depends_on, mode=spec.mode, parent=spec.parent,
        branch=project.target_branch or "",
        required_by=tuple(k for k in reg.keys() if key in reg.get(k).spec.depends_on),
    )
    try:
        s.stale = scope.freshen(key)
        st = project.status
        s.sha = (st.sha or "")[:7]
        now = time.time()
        s.last_fetch_age = int(now - st.last_fetch) if st.last_fetch else None
        s.last_push_age = int(now - st.last_push) if st.last_push else None
        s.sync_error = st.last_error
        s.pushes = st.pushes
        s.pr_url = st.pr_url or ""
        root = project.planning_root
        pfile = root / "PRODUCT.MD"
        if not pfile.exists():
            s.error = f"no PRODUCT.MD in {spec.planning_dir or 'the repository root'}"
            return s
        _fill_content(s, project, root, st.sha)

        waiting = inbox_lib.list_items(root)
        s.inbox_count = len(waiting)
        s.inbox_oldest_days = max((i.age_days for i in waiting), default=0)
        s.inbox_stale = sum(1 for i in waiting if i.stale)

    except Exception as exc:  # one broken project must not take down the page
        s.error = str(exc) or exc.__class__.__name__
    return s


def summarize_all() -> list[ProjectSummary]:
    """Summaries in manifest order. Blocking; call from a worker thread."""
    reg = scope.get_registry()
    keys = reg.keys()
    if not keys:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(keys))) as pool:
        return list(pool.map(_summarize, keys))


def _feature_rows(key: str) -> tuple[list[dict], str | None]:
    reg = scope.get_registry()
    try:
        scope.freshen(key)
        product = product_parser._parse_text((reg.get(key).planning_root / "PRODUCT.MD").read_text(encoding="utf-8"))
    except Exception as exc:  # one broken project must not take down the page
        return [], str(exc) or exc.__class__.__name__
    rows = []
    for area in product.wbs_areas:
        for sub in area.sub_areas:
            for f in sub.features:
                stage = "In-Progress" if f.status == FeatureStatus.planned else ("Idea" if f.stage == "Gap" else f.stage)
                rows.append({"project": key, "wbs": f.wbs, "name": f.name, "area": f"{sub.wbs_prefix} {sub.title}",
                             "stage": stage, "status": "Idea" if stage in ("Idea", "Scored") else stage,
                             "owner": f.owner or "", "notes": f.notes, "flagged": f.flagged})
    return rows, None


def program_features() -> tuple[list[dict], dict[str, str]]:
    """Every feature of every member project, in manifest order, for the cross-project
    editing page; and {project: error} for the ones that could not be read."""
    keys = scope.get_registry().keys()
    if not keys:
        return [], {}
    with ThreadPoolExecutor(max_workers=min(8, len(keys))) as pool:
        results = list(pool.map(_feature_rows, keys))
    rows = [r for part, _ in results for r in part]
    return rows, {k: err for k, (_, err) in zip(keys, results) if err}


def initiative_views(projects: list[ProjectSummary]) -> tuple[list[ProgramInitiativeView], str | None]:
    """Roll each program initiative up from the member features' current stage.

    Returns (views, error). A manifest that cannot be read yields no views and a
    message; a member whose project failed to load or whose WBS no longer exists
    is listed as missing and counts toward the total.
    """
    reg = scope.get_registry()
    try:
        inits = reg.initiatives()
    except Exception as exc:
        return [], str(exc)
    by_key = {p.key: p for p in projects}
    upstream = {k: _transitive_deps(reg, k) for k in reg.keys()}
    views = []
    for ini in inits:
        v = ProgramInitiativeView(ini.name, ini.kind)
        for key, wbs in ini.members:
            proj = by_key.get(key)
            hit = proj.stages_by_wbs.get(wbs) if proj else None
            name, stage = hit if hit else ("", "")
            is_done = stage in ("Live", "Released")
            v.members.append({"project": key, "wbs": wbs, "name": name, "stage": stage or "Unavailable",
                              "done": is_done, "missing": hit is None, "blocked_by": []})
            v.total += 1
            v.done += is_done
        # A member waits on any unfinished member, in the same initiative, of a
        # project it depends on (directly or through other projects).
        for m in v.members:
            if m["done"]:
                continue
            for other in v.members:
                if other is not m and not other["done"] and other["project"] in upstream.get(m["project"], ()):
                    m["blocked_by"].append(f"{other['project']}:{other['wbs']}")
            if m["blocked_by"] and m["project"] in by_key:
                by_key[m["project"]].blocked.append(f"{m['wbs']} waits on {', '.join(m['blocked_by'])} ({ini.name})")
        views.append(v)
    return views, None


def _transitive_deps(reg, key: str) -> set[str]:
    seen: set[str] = set()
    stack = list(reg.get(key).spec.depends_on)
    while stack:
        d = stack.pop()
        if d not in seen:
            seen.add(d)
            stack.extend(reg.get(d).spec.depends_on)
    return seen
