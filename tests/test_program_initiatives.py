"""Program initiatives: same-named initiatives unify, Aliases:/Not: refine that, completed ones are set aside."""
from types import SimpleNamespace

import pytest

from app import overview, scope
from app.manifest import ManifestError, normalize_name, parse_initiatives
from app.overview import ProjectSummary

KNOWN = {"web", "app", "api"}


def test_names_match_regardless_of_case_and_spacing():
    assert normalize_name("  Faster   Checkout. ") == normalize_name("faster checkout")
    assert normalize_name("Faster Checkout") != normalize_name("Faster Checkouts")


def test_aliases_and_not_lines_are_read():
    inits = parse_initiatives("""## Initiatives

### Faster Checkout (Major)
Aliases: One-Tap Pay, `Checkout v2`
Not: web
- api:1.1.1
""", KNOWN)
    (i,) = inits
    assert i.aliases == ("One-Tap Pay", "Checkout v2") and i.excludes == ("web",)
    assert i.members == (("api", "1.1.1"),)


def test_not_naming_an_unknown_project_is_an_error():
    with pytest.raises(ManifestError, match="excludes unknown project 'nope'"):
        parse_initiatives("## Initiatives\n\n### X\nNot: nope\n", KNOWN)


def test_old_manifests_without_the_new_lines_parse_as_before():
    (i,) = parse_initiatives("## Initiatives\n\n### X (Minor)\n- web:1.1.1\n", KNOWN)
    assert i.aliases == () and i.excludes == ()


# ── unification ──────────────────────────────────────────────────────────────

def _project(key, stages, initiatives=()):
    """stages: {wbs: stage}; initiatives: [(name, kind, [wbs])]"""
    return ProjectSummary(key=key, stages_by_wbs={w: (f"feat {w}", st) for w, st in stages.items()},
                          initiative_members=[(n, k, list(c)) for n, k, c in initiatives])


class FakeRegistry:
    def __init__(self, program_text="", deps=None):
        self._inits = parse_initiatives(program_text, KNOWN) if program_text else []
        self._deps = deps or {}

    def initiatives(self):
        return self._inits

    def keys(self):
        return list(KNOWN)

    def get(self, key):
        return SimpleNamespace(spec=SimpleNamespace(depends_on=tuple(self._deps.get(key, ()))))


@pytest.fixture()
def reg():
    def install(program_text="", deps=None):
        scope.set_registry(FakeRegistry(program_text, deps))
    yield install
    scope.set_registry(None)


def by_name(views):
    return {v.name: v for v in views}


def test_same_named_project_initiatives_become_one(reg):
    reg()
    web = _project("web", {"1.1": "Live", "1.2": "In-Progress"}, [("Faster Checkout", "major", ["1.1", "1.2"])])
    app = _project("app", {"2.1": "Idea"}, [("faster checkout", "minor", ["2.1"])])
    views, err = overview.unified_initiatives([web, app])
    assert err is None and len(views) == 1
    v = views[0]
    assert (v.done, v.total, v.kind, v.status) == (1, 3, "major", "In progress")
    assert v.kinds_differ and v.merged_from == ["app", "web"]
    assert {m["source"] for m in v.members} == {"matched"}
    assert {p["project"]: (p["done"], p["total"]) for p in v.projects} == {"web": (1, 2), "app": (0, 1)}


def test_declared_members_and_project_roadmaps_are_deduplicated(reg):
    reg("## Initiatives\n\n### Faster Checkout (Major)\n- web:1.1\n- api:3.1\n")
    web = _project("web", {"1.1": "Live"}, [("Faster Checkout", "major", ["1.1"])])
    api = _project("api", {"3.1": "Idea"})
    (v,) = overview.unified_initiatives([web, api])[0]
    assert v.total == 2 and v.declared
    assert {(m["project"], m["source"]) for m in v.members} == {("web", "declared"), ("api", "declared")}


def test_alias_folds_a_differently_named_initiative_in(reg):
    reg("## Initiatives\n\n### Faster Checkout (Major)\nAliases: One-Tap Pay\n- api:3.1\n")
    app = _project("app", {"2.1": "In-Progress"}, [("One-Tap Pay", "minor", ["2.1"])])
    api = _project("api", {"3.1": "Idea"})
    views, _ = overview.unified_initiatives([app, api])
    assert [v.name for v in views] == ["Faster Checkout"] and views[0].total == 2


def test_not_keeps_one_projects_initiative_separate(reg):
    reg("## Initiatives\n\n### Faster Checkout (Major)\nNot: web\n- api:3.1\n")
    web = _project("web", {"1.1": "In-Progress"}, [("Faster Checkout", "major", ["1.1"])])
    app = _project("app", {"2.1": "Idea"}, [("Faster Checkout", "major", ["2.1"])])
    api = _project("api", {"3.1": "Idea"})
    views = by_name(overview.unified_initiatives([web, app, api])[0])
    assert set(views) == {"Faster Checkout", "Faster Checkout (web)"}
    assert views["Faster Checkout (web)"].separate_of == "web" and views["Faster Checkout (web)"].total == 1
    assert views["Faster Checkout"].merged_from == ["api", "app"]


def test_completed_initiatives_are_marked_and_sorted_last(reg):
    reg()
    web = _project("web", {"1.1": "Live", "1.2": "Released", "1.3": "In-Progress", "1.4": "Idea"},
                   [("Done Thing", "minor", ["1.1", "1.2"]), ("Going", "minor", ["1.3"]),
                    ("Later", "minor", ["1.4"])])
    views, _ = overview.unified_initiatives([web])
    assert [(v.name, v.status) for v in views] == [("Going", "In progress"), ("Later", "Planned"), ("Done Thing", "Complete")]


def test_a_missing_member_keeps_an_initiative_from_completing(reg):
    reg("## Initiatives\n\n### X\n- web:1.1\n- web:9.9\n")
    (v,) = overview.unified_initiatives([_project("web", {"1.1": "Live"})])[0]
    assert v.status == "In progress" and any(m["missing"] for m in v.members)


def test_blocked_and_uat_sort_first_within_a_status(reg):
    reg(deps={"web": ["api"]})
    web = _project("web", {"1.1": "In-Progress"}, [("Blocked One", "minor", ["1.1"])])
    api = _project("api", {"3.1": "In-Progress", "3.2": "In-Progress", "3.3": "Live"},
                   [("Blocked One", "minor", ["3.1"]), ("Plain", "minor", ["3.2", "3.3"])])
    views, _ = overview.unified_initiatives([web, api])
    assert [v.name for v in views] == ["Blocked One", "Plain"]
    assert views[0].blocked == 1 and "1.1 waits on api:3.1" in web.blocked[0]


def test_a_draft_without_members_is_a_draft_and_leaves_drafts_when_a_project_defines_work(reg):
    reg("## Initiatives\n\n### Search (Major, Draft)\nGoal: Find.\nAsk web: Page\nAsk api: Index\n")
    web, api = _project("web", {}), _project("api", {})
    (v,) = overview.unified_initiatives([web, api])[0]
    assert v.status == "Draft" and v.draft and v.goal == "Find."
    assert [(a["project"], a["state"]) for a in v.asks] == [("web", "Drafted"), ("api", "Drafted")]
    web.initiative_members = [("search", "major", ["1.1", "1.2"])]
    web.stages_by_wbs = {"1.1": ("a", "Idea"), "1.2": ("b", "Idea")}
    (v,) = overview.unified_initiatives([web, api])[0]
    assert v.status == "Planned" and (v.total, v.done) == (2, 0)
    assert dict((a["project"], a["state"]) for a in v.asks) == {"web": "Defined", "api": "Drafted"}


def _task(state, created="2026-01-01T00:00:00Z", **kw):
    return {"initiative": "Search", "state": state, "created": created, "age_days": 3, "stale": False, "reason": "", **kw}


def test_ask_states_follow_the_projects_inbox_and_roadmap():
    names = {"search"}
    state = lambda p: overview.ask_state(p, names)
    assert state(_project("web", {})) == ("Drafted", "")
    p = _project("web", {}); p.initiative_tasks = [_task("open")]
    assert state(p) == ("Sent", "waiting 3d")
    p.initiative_members = [("Search", "major", [])]
    assert state(p)[0] == "Breaking out"
    p.initiative_tasks = [_task("promoted")]; p.initiative_members = [("Search", "major", ["1.1", "1.2"])]
    assert state(p) == ("Defined", "2 features")
    p.initiative_tasks = [_task("declined", reason="not ours")]; p.initiative_members = []
    assert state(p) == ("Declined", "not ours")
    p.initiative_tasks = [_task("declined", reason="not ours"), _task("open", created="2026-02-01T00:00:00Z")]
    assert state(p)[0] == "Sent"                       # resent after a decline: the newest task counts
    p.initiative_tasks = [_task("declined", reason="x"), _task("open")]
    assert state(p)[0] == "Sent"                       # even when both carry the same timestamp
    broken = _project("web", {}); broken.error = "boom"
    assert state(broken) == ("Unavailable", "boom") and state(None)[0] == "Unavailable"


def test_an_initiative_with_requirements_still_pending_is_not_complete(reg):
    reg("## Initiatives\n\n### Search (Major)\n- web:1.1\nAsk web: x\nAsk api: y\n")
    web = _project("web", {"1.1": "Live"})
    api = _project("api", {}); api.initiative_tasks = [_task("open")]
    (v,) = overview.unified_initiatives([web, api])[0]
    assert (v.done, v.total) == (1, 1) and v.pending == ["api"] and v.status == "In progress"
    api.initiative_tasks = [_task("declined", reason="n/a")]
    (v,) = overview.unified_initiatives([web, api])[0]
    assert v.status == "Complete"


def test_asks_are_listed_dependencies_first_and_know_what_they_wait_on(reg):
    reg("## Initiatives\n\n### S (Draft)\nAsk web: a\nAsk api: b\n", deps={"web": ["api"]})
    (v,) = overview.unified_initiatives([_project("web", {}), _project("api", {})])[0]
    assert [a["project"] for a in v.asks] == ["api", "web"]
    assert [a["waiting_on"] for a in v.asks] == [[], ["api"]]
