"""PROGRAM.MD kept in a git repository: cloned, reloaded when it changes, and written through the shared writer."""
import os
import subprocess
from functools import partial
from pathlib import Path

import pytest

from app import poller
from app.manifest import (ManifestError, parse_initiatives, transform_add_initiative,
                          transform_remove_initiative, transform_update_initiative)
from app.program_manifest import edit_manifest
from app.registry import MANIFEST_KEY, Registry, run_git
from app.writer import Actor

TABLE = """# Program

## Projects

| Key | Repo / Path | Role | Depends On |
| --- | ----------- | ---- | ---------- |
| web | {web} | Web | |

## Backlog
- later
"""


def git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env)


def make_repo(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    bare = tmp_path / f"{name}.git"
    bare.mkdir()
    git(bare, "init", "-q", "--bare", "-b", "main")
    seed = tmp_path / f"seed-{name}"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    for rel, text in files.items():
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed / rel).write_text(text)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "-q", "origin", "main")
    return bare


def push_change(tmp_path: Path, bare: Path, rel: str, text: str) -> None:
    work = tmp_path / f"work-{bare.stem}"
    if not work.exists():
        git(tmp_path, "clone", "-q", str(bare), str(work))
    git(work, "pull", "-q")
    (work / rel).write_text(text)
    git(work, "commit", "-qam", "edit")
    git(work, "push", "-q", "origin", "main")


@pytest.fixture()
def setup(tmp_path):
    web = make_repo(tmp_path, "web", {"PRODUCT.MD": "# Web - Product Overview\n"})
    prog = make_repo(tmp_path, "program", {"deploy/PROGRAM.MD": TABLE.format(web=web)})
    reg = Registry.from_manifest_repo(str(prog), "deploy/PROGRAM.MD", cache_dir=tmp_path / "cache")
    return reg, prog, web, tmp_path


def remote_manifest(prog: Path) -> str:
    return run_git(prog, ["show", "main:deploy/PROGRAM.MD"])


def test_projects_come_from_the_cloned_manifest(setup):
    reg, *_ = setup
    assert reg.keys() == ["web"]
    assert MANIFEST_KEY not in reg           # the manifest's own clone is never served as a project
    assert reg.get(MANIFEST_KEY).spec.repo.endswith("program.git")


def test_a_change_is_a_commit_by_the_person_and_shows_at_once(setup):
    reg, prog, *_ = setup
    r = edit_manifest(reg, partial(transform_add_initiative, name="Search", kind="major", members=[("web", "1.1.1")]),
                      "UI: add initiative Search", Actor("Ada", "ada@example.com"))
    assert r.changed and r.versioned
    assert "### Search (Major)\n- web:1.1.1" in remote_manifest(prog)
    assert run_git(prog, ["log", "-1", "--format=%an <%ae>|%s", "main"]) == "Ada <ada@example.com>|UI: add initiative Search"
    assert [(i.name, i.members) for i in reg.initiatives()] == [("Search", (("web", "1.1.1"),))]
    assert "| web |" in remote_manifest(prog)           # the Projects table is untouched


def test_a_bad_request_changes_nothing_and_is_not_a_sync_failure(setup):
    reg, prog, *_ = setup
    edit_manifest(reg, partial(transform_add_initiative, name="Search"), "add")
    before = run_git(prog, ["rev-parse", "main"])
    with pytest.raises(ManifestError, match="already exists"):
        edit_manifest(reg, partial(transform_add_initiative, name="search"), "add again")
    assert run_git(prog, ["rev-parse", "main"]) == before
    assert reg.manifest_project.status.last_error is None


def test_reload_adopts_a_changed_project_table_and_survives_a_broken_one(setup):
    reg, prog, web, tmp = setup
    other = make_repo(tmp, "api", {"PRODUCT.MD": "# Api - Product Overview\n"})
    push_change(tmp, prog, "deploy/PROGRAM.MD",
                TABLE.format(web=web).replace("| web |", f"| api | {other} | Api | |\n| web |"))
    assert reg.reload_manifest() is None
    assert reg.keys() == ["api", "web"]
    push_change(tmp, prog, "deploy/PROGRAM.MD", "no table here")
    assert "no '## Projects' section" in reg.reload_manifest()
    assert reg.keys() == ["api", "web"] and reg.manifest_error     # the last good list stays in service
    push_change(tmp, prog, "deploy/PROGRAM.MD", TABLE.format(web=web))
    assert reg.reload_manifest() is None and reg.manifest_error is None and reg.keys() == ["web"]


def test_the_push_webhook_for_the_manifest_repository_reloads_it(setup):
    reg, prog, web, tmp = setup
    push_change(tmp, prog, "deploy/PROGRAM.MD", TABLE.format(web=web) + "\n## Initiatives\n\n### Late (Minor)\n")
    out = poller.handle_push_event(reg, {"repository": {"clone_url": str(prog)}})
    assert out == {"program manifest": None}
    assert [i.name for i in reg.initiatives()] == ["Late"]


def test_a_plain_local_manifest_is_edited_in_place(tmp_path):
    path = tmp_path / "PROGRAM.MD"
    path.write_text(TABLE.format(web="x"))
    reg = Registry([], tmp_path / "cache")
    reg.manifest_path = path
    r = edit_manifest(reg, partial(transform_add_initiative, name="Search"), "m")
    assert r.changed and not r.versioned and "### Search (Minor)" in path.read_text()
    assert not edit_manifest(reg, lambda t: t, "m").changed


# ── transforms ──────────────────────────────────────────────────────────────

BASE = TABLE.format(web="x")
FULL = BASE.replace("## Backlog", """## Initiatives

Prose before the first heading stays.

### One (Major)
A description line.
- web:1.1.1 - because
- web:1.1.2

<!-- ### Hidden (Minor)
- web:9.9.9 -->

### Two
- web:2.1.1

## Backlog""")


def test_add_creates_the_section_after_the_projects_table_and_before_backlog():
    out = transform_add_initiative(BASE, "Search", "Major", [("web", "1.1.1")])
    assert out.index("## Projects") < out.index("## Initiatives") < out.index("## Backlog")
    assert parse_initiatives(out)[0].members == (("web", "1.1.1"),)
    assert transform_add_initiative("## Projects\n\n| Key | Repo / Path |\n|-|-|\n| a | x |", "N").rstrip().endswith("### N (Minor)")


def test_add_appends_after_the_last_initiative():
    out = transform_add_initiative(FULL, "Three", "Minor", [("web", "3.1.1")], aliases=["Trois"])
    names = [i.name for i in parse_initiatives(out)]
    assert names == ["One", "Two", "Three"] and "## Backlog\n- later" in out
    assert parse_initiatives(out)[2].aliases == ("Trois",)


def test_update_changes_only_what_it_is_given_and_keeps_other_lines():
    out = transform_update_initiative(FULL, "one", kind="minor", members=[("web", "1.1.2"), ("web", "1.1.3")], excludes=["web"])
    assert "A description line." in out and "<!-- ### Hidden (Minor)" in out and "Prose before" in out
    one = parse_initiatives(out)[0]
    assert (one.kind, one.members, one.excludes) == ("minor", (("web", "1.1.2"), ("web", "1.1.3")), ("web",))
    assert "- web:1.1.1 - because" not in out
    kept = transform_update_initiative(FULL, "One", members=[("web", "1.1.1"), ("web", "1.1.2")])
    assert "- web:1.1.1 - because" in kept          # an unchanged member keeps its own line
    assert transform_update_initiative(FULL, "Two") == FULL


def test_remove_deletes_only_that_block():
    out = transform_remove_initiative(FULL, "One")
    assert [i.name for i in parse_initiatives(out)] == ["Two"]
    assert "Prose before" in out and "## Backlog\n- later" in out


def test_commented_out_initiatives_are_not_found_and_bad_input_is_refused():
    with pytest.raises(ManifestError, match="no initiative named 'Hidden'"):
        transform_update_initiative(FULL, "Hidden", kind="major")
    with pytest.raises(ManifestError, match="already exists"):
        transform_add_initiative(FULL, "two")
    for bad in ("", "  ", "X (Major)", "a <!-- b"):
        with pytest.raises(ManifestError):
            transform_add_initiative(FULL, bad)
    with pytest.raises(ManifestError):
        transform_add_initiative(FULL, "Ok", members=[("Web Proj", "1.1")])
    with pytest.raises(ManifestError):
        transform_add_initiative(FULL, "Ok", members=[("web", "one")])


# ── drafts: heading tag, Goal/Success/Ask lines ─────────────────────────────

def test_draft_fields_round_trip_through_add_and_update():
    out = transform_add_initiative(FULL, "Search Overhaul", "major", draft=True, goal="Find things.",
                                   success="Under 300 ms", asks=[("web", "Results page"), ("api", "")])
    assert "### Search Overhaul (Major, Draft)\nGoal: Find things.\nSuccess: Under 300 ms\nAsk web: Results page\nAsk api:" in out
    (d,) = [i for i in parse_initiatives(out) if i.name == "Search Overhaul"]
    assert (d.kind, d.draft, d.goal, d.success, d.asks) == ("major", True, "Find things.", "Under 300 ms", (("web", "Results page"), ("api", "")))
    out = transform_update_initiative(out, "search overhaul", draft=False, goal="Better.", asks=[("api", "Index")])
    (d,) = [i for i in parse_initiatives(out) if i.name == "Search Overhaul"]
    assert (d.kind, d.draft, d.goal, d.success, d.asks) == ("major", False, "Better.", "Under 300 ms", (("api", "Index"),))
    assert transform_update_initiative(out, "Search Overhaul", asks=[]).count("Ask ") == 0


def test_a_trailing_parenthesis_that_is_not_a_tag_stays_in_the_name():
    (i,) = parse_initiatives("## Initiatives\n\n### Pricing (EMEA) (Major, Draft)\n### Plain (EMEA)\n")[:1]
    assert (i.name, i.kind, i.draft) == ("Pricing (EMEA)", "major", True)
    assert parse_initiatives("## Initiatives\n\n### Plain (EMEA)\n")[0].name == "Plain (EMEA)"
    with pytest.raises(ManifestError):
        transform_add_initiative(FULL, "Odd (Draft)")


def test_an_ask_for_an_unknown_project_is_an_error():
    with pytest.raises(ManifestError, match="unknown project 'nope'"):
        parse_initiatives("## Initiatives\n\n### X (Draft)\nAsk nope: hi\n", {"web"})
