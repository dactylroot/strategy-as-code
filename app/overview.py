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
from .manifest import normalize_name
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
    initiative_members: list[tuple[str, str, list[str]]] = field(default_factory=list)  # ABOUT.MD: name, kind, WBS codes
    initiative_tasks: list[dict] = field(default_factory=list)  # planning tasks sent here: initiative, state, created, age_days, stale, reason


@dataclass
class ProgramInitiativeView:
    """One initiative as the program sees it: the declared PROGRAM.MD initiative and every
    project's same-named ABOUT.MD initiative, folded into one."""
    name: str
    kind: str
    status: str = "Planned"              # Planned | In progress | Complete
    done: int = 0
    total: int = 0
    awaiting_uat: int = 0
    blocked: int = 0
    kinds_differ: bool = False
    members: list[dict] = field(default_factory=list)   # project, wbs, name, stage, done, missing, blocked_by, source
    projects: list[dict] = field(default_factory=list)  # project, done, total
    declared: bool = False               # listed in PROGRAM.MD (otherwise found only in project roadmaps)
    draft: bool = False                  # tagged Draft in PROGRAM.MD (it shows as status Draft until a project has work for it)
    goal: str = ""
    success: str = ""
    asks: list[dict] = field(default_factory=list)      # project, text, state, detail, waiting_on: the planning task each project is asked to do
    pending: list[str] = field(default_factory=list)    # projects still to define their part (task sent, not yet broken out)
    separate_of: str = ""                # a project's initiative kept apart from a same-named one by `Not:`
    snippets: dict[str, str] = field(default_factory=dict)  # PROGRAM.MD lines that merge or split, for copying

    @property
    def pct(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0

    @property
    def merged_from(self) -> list[str]:
        return sorted({m["project"] for m in self.members})

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in (
            "name", "kind", "status", "done", "total", "awaiting_uat", "blocked", "kinds_differ", "members", "projects",
            "declared", "draft", "goal", "success", "asks", "pending", "separate_of")}
        return {**d, "pct": self.pct, "merged_from": self.merged_from}


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
            s.initiative_members.append((ini.name, ini.kind, [f.wbs for f in members]))
        if about.changelog:
            s.version = about.changelog[0].version


_CONTENT_FIELDS = ("title", "features", "total", "done_pct", "in_progress", "awaiting_uat", "flagged",
                   "bugs", "bug_total", "bug_status", "initiatives", "version", "stages_by_wbs", "initiative_members")
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
        s.initiative_tasks = ([{"initiative": i.initiative, "state": "open", "created": i.created, "age_days": i.age_days,
                                "stale": i.stale, "reason": ""} for i in waiting if i.kind == inbox_lib.TASK]
                              + [{"initiative": i.initiative, "state": i.outcome, "created": i.created, "age_days": i.age_days,
                                  "stale": False, "reason": i.reason}
                                 for i in inbox_lib.list_triaged(root) if i.kind == inbox_lib.TASK])

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


_STAGE_DONE = ("Live", "Released")
_RANK = {"In progress": 0, "Planned": 1, "Draft": 2, "Complete": 3}
ASK_OPEN = ("Sent", "Breaking out")     # a project has been asked and has not yet defined its part


def ask_state(project: ProjectSummary | None, names: set[str]) -> tuple[str, str]:
    """Where one project stands on a planning task for the initiative called any of `names` (normalized):
    Drafted (not sent), Sent, Breaking out (the project has started its own initiative; the task is still
    open), Defined, or Declined. Read from the project's inbox and roadmap, so nothing about it is stored."""
    if project is None or project.error:
        return "Unavailable", (project.error if project else "") or "project not loaded"
    tasks = sorted((t for t in project.initiative_tasks if normalize_name(t["initiative"]) in names),
                   key=lambda t: (t["created"], t["state"] == "open"))   # a resend in the same second still counts as newest
    own = [m for m in project.initiative_members if normalize_name(m[0]) in names]
    features = sum(len(m[2]) for m in own)
    last = tasks[-1] if tasks else None
    if last and last["state"] == "open":
        wait = f"waiting {last['age_days']}d" + (" (stale)" if last["stale"] else "")
        return ("Breaking out" if own else "Sent"), wait
    if last and last["state"] in ("declined", "duplicate"):
        return "Declined", last["reason"] or last["state"]
    if last or features:
        return "Defined", f"{features} feature{'' if features == 1 else 's'}"
    return "Drafted", ""


class _Group:
    def __init__(self, name: str, kind: str, declared: bool, excludes=(), separate_of: str = "") -> None:
        self.name, self.declared, self.excludes, self.separate_of = name, declared, set(excludes), separate_of
        self.names: set[str] = {normalize_name(name)}      # the name and its aliases, normalized
        self.draft, self.goal, self.success = False, "", ""
        self.asks: dict[str, str] = {}                      # project -> hint
        self.kinds: set[str] = {kind} if kind else set()
        self.members: dict[tuple[str, str], str] = {}     # (project, wbs) -> source

    def add(self, project: str, wbs: str, source: str) -> None:
        self.members.setdefault((project, wbs), source)    # an explicit PROGRAM.MD member keeps its source


def _unify(program_inits, projects: list[ProjectSummary]) -> list[_Group]:
    """Group program initiatives and projects' own initiatives by name.

    A project initiative joins the group whose name or alias matches it, unless that group
    names the project in `Not:`, in which case it stands alone. One that matches no declared
    group starts a group of its own, so projects sharing a name still unify without PROGRAM.MD.
    """
    groups: list[_Group] = []
    by_key: dict[str, _Group] = {}
    for ini in program_inits:
        g = by_key.get(normalize_name(ini.name))
        if g is None:
            g = _Group(ini.name, ini.kind, True)
            groups.append(g)
        else:
            g.kinds.add(ini.kind)
        g.excludes |= set(ini.excludes)
        g.draft, g.goal, g.success = g.draft or ini.draft, g.goal or ini.goal, g.success or ini.success
        for project, hint in ini.asks:
            g.asks.setdefault(project, hint)
        for key in (ini.name, *ini.aliases):
            by_key.setdefault(normalize_name(key), g)
            g.names.add(normalize_name(key))
        for project, wbs in ini.members:
            g.add(project, wbs, "declared")
    for p in projects:
        if p.error:
            continue
        for name, kind, codes in p.initiative_members:
            key = normalize_name(name)
            g = by_key.get(key)
            if g is not None and p.key in g.excludes:
                solo = by_key.get(f"{key}\0{p.key}")
                if solo is None:
                    solo = by_key[f"{key}\0{p.key}"] = _Group(f"{name} ({p.key})", kind, False, separate_of=p.key)
                    groups.append(solo)
                g = solo
            elif g is None:
                g = by_key[key] = _Group(name, kind, False)
                groups.append(g)
            g.kinds.add(kind)
            for code in codes:
                g.add(p.key, code, "matched")
    return groups


def unified_initiatives(projects: list[ProjectSummary]) -> tuple[list[ProgramInitiativeView], str | None]:
    """Program initiatives rolled up from the member features' current stage, sorted for oversight:
    in-progress before planned, then blocked, then awaiting UAT, then nearest to done. Complete ones
    are returned too (the page hides them) so they can be offered on request.

    Returns (views, error). A manifest that cannot be read yields no views and a message; a member
    whose project failed to load or whose WBS no longer exists is listed as missing and counts toward
    the total.
    """
    reg = scope.get_registry()
    try:
        inits = reg.initiatives()
    except Exception as exc:
        return [], str(exc)
    by_key = {p.key: p for p in projects}
    upstream = {k: _transitive_deps(reg, k) for k in reg.keys()}
    views = []
    for g in _unify(inits, projects):
        v = ProgramInitiativeView(g.name, "major" if "major" in g.kinds else "minor", declared=g.declared,
                                  kinds_differ=len(g.kinds) > 1, separate_of=g.separate_of)
        for (key, wbs), source in g.members.items():
            proj = by_key.get(key)
            hit = proj.stages_by_wbs.get(wbs) if proj else None
            name, stage = hit if hit else ("", "")
            is_done = stage in _STAGE_DONE
            v.members.append({"project": key, "wbs": wbs, "name": name, "stage": stage or "Unavailable",
                              "done": is_done, "missing": hit is None, "blocked_by": [], "source": source})
            v.total += 1
            v.done += is_done
            v.awaiting_uat += stage == "Live"
        v.members.sort(key=lambda m: (list(by_key).index(m["project"]) if m["project"] in by_key else 99,
                                      [int(x) for x in m["wbs"].split(".")]))
        # A member waits on any unfinished member, in the same initiative, of a
        # project it depends on (directly or through other projects).
        for m in v.members:
            if m["done"]:
                continue
            for other in v.members:
                if other is not m and not other["done"] and other["project"] in upstream.get(m["project"], ()):
                    m["blocked_by"].append(f"{other['project']}:{other['wbs']}")
            if m["blocked_by"]:
                v.blocked += 1
                if m["project"] in by_key:
                    by_key[m["project"]].blocked.append(f"{m['wbs']} waits on {', '.join(m['blocked_by'])} ({v.name})")
        for key in dict.fromkeys(m["project"] for m in v.members):
            mine = [m for m in v.members if m["project"] == key]
            v.projects.append({"project": key, "done": sum(m["done"] for m in mine), "total": len(mine)})
        v.draft, v.goal, v.success = g.draft, g.goal, g.success
        for key, hint in sorted(g.asks.items(), key=lambda kv: len(upstream.get(kv[0], ()))):   # dependencies first
            state, detail = ask_state(by_key.get(key), g.names)
            v.asks.append({"project": key, "text": hint, "state": state, "detail": detail})
        for a in v.asks:
            a["waiting_on"] = [o["project"] for o in v.asks if o["project"] in upstream.get(a["project"], ()) and o["state"] != "Defined"]
        v.pending = [a["project"] for a in v.asks if a["state"] in ASK_OPEN]
        done_all = bool(v.total) and v.done == v.total
        started = any(m["stage"] == "In-Progress" for m in v.members) or 0 < v.done < v.total
        v.status = ("Draft" if v.draft and not v.total else
                    "Complete" if done_all and not v.pending else
                    "In progress" if started or done_all else "Planned")
        v.snippets = _snippets(v)
        views.append(v)
    views.sort(key=lambda v: (_RANK[v.status], not v.blocked, not v.awaiting_uat, -v.pct, v.name.lower()))
    return views, None


def _snippets(v: ProgramInitiativeView) -> dict[str, str]:
    """The PROGRAM.MD lines that would change how `v` is folded, offered for copying: the manifest is
    edited by hand, so the page shows the text rather than writing it."""
    out: dict[str, str] = {}
    if v.separate_of:
        out["merge"] = f"Remove {v.separate_of} from the Not: line under the initiative this was split from"
        return out
    if len({m["project"] for m in v.members}) > 1:
        for proj in v.merged_from:
            out[f"split:{proj}"] = f"### {v.name}\nNot: {proj}"
    out["alias"] = f"### {v.name}\nAliases: <other name>"
    return out


def _transitive_deps(reg, key: str) -> set[str]:
    seen: set[str] = set()
    stack = list(reg.get(key).spec.depends_on)
    while stack:
        d = stack.pop()
        if d not in seen:
            seen.add(d)
            stack.extend(reg.get(d).spec.depends_on)
    return seen
