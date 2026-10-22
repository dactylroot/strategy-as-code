"""Program overview: one summary row per member project.

Reads each project's planning files from its managed clone, refreshing the
clones concurrently. A project that cannot be read yields an error row; it never
hides the others.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import inbox as inbox_lib
from . import scope
from .models import BugSeverity, FeatureStatus
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
    members: list[dict] = field(default_factory=list)   # project, wbs, name, stage, done, missing


STAGES = ("Idea", "Scored", "In-Progress", "Live", "Released")


def _summarize(key: str) -> ProjectSummary:
    reg = scope.get_registry()
    project = reg.get(key)
    spec = project.spec
    s = ProjectSummary(
        key=key, role=spec.role, depends_on=spec.depends_on, mode=spec.mode,
        branch=project.target_branch or "",
        required_by=tuple(k for k in reg.keys() if key in reg.get(k).spec.depends_on),
    )
    try:
        s.stale = scope.freshen(key)
        s.sha = (project.status.sha or "")[:7]
        root = project.planning_root
        pfile = root / "PRODUCT.MD"
        if not pfile.exists():
            s.error = f"no PRODUCT.MD in {spec.planning_dir or 'the repository root'}"
            return s
        product = product_parser._parse_text(pfile.read_text(encoding="utf-8"))
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

        waiting = inbox_lib.list_items(root)
        s.inbox_count = len(waiting)
        s.inbox_oldest_days = max((i.age_days for i in waiting), default=0)
        s.inbox_stale = sum(1 for i in waiting if i.stale)

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
    views = []
    for ini in inits:
        v = ProgramInitiativeView(ini.name, ini.kind)
        for key, wbs in ini.members:
            proj = by_key.get(key)
            hit = proj.stages_by_wbs.get(wbs) if proj else None
            name, stage = hit if hit else ("", "")
            is_done = stage in ("Live", "Released")
            v.members.append({"project": key, "wbs": wbs, "name": name, "stage": stage or "Unavailable",
                              "done": is_done, "missing": hit is None})
            v.total += 1
            v.done += is_done
        views.append(v)
    return views, None
