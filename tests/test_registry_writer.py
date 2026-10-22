import os
import subprocess
from pathlib import Path

import pytest

from app.models import FeatureStatus
from app.manifest import ManifestError, ProjectSpec, parse_manifest
from app.parsers.product import transform_feature_status
from app.registry import Registry, RegistryError, run_git
from app.writer import Actor, WriteConflict, WriteError, edit, edit_file

PRODUCT = """\
# Demo

## Features

### 1. Core

#### 1.1 Auth

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.1.1 | Login | Idea | 8 | 3 | first |
| 1.1.2 | Logout | Idea | 5 | 2 | second |
"""


def _git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env)


@pytest.fixture()
def origin(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    bare.mkdir()
    _git(bare, "init", "-q", "--bare", "-b", "main")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "PRODUCT.MD").write_text(PRODUCT, encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-q", "origin", "main")
    return bare


def make_registry(tmp_path: Path, origin: Path, **spec) -> Registry:
    return Registry([ProjectSpec(key="demo", repo=str(origin), **spec)], tmp_path / "cache")


def clone_of(tmp_path: Path, origin: Path, name: str = "other") -> Path:
    path = tmp_path / name
    run_git(None, ["clone", "-q", str(origin), str(path)])
    return path


def remote_file(origin: Path, ref: str, name: str = "PRODUCT.MD") -> str:
    return run_git(origin, ["show", f"{ref}:{name}"])


def live(wbs: str):
    return lambda t: transform_feature_status(t, wbs, FeatureStatus.live)


def test_edit_file_pushes_a_commit_authored_by_the_actor(tmp_path, origin):
    reg = make_registry(tmp_path, origin)
    actor = Actor("Ada Lovelace", "ada@example.com")
    res = edit_file(reg, "demo", "PRODUCT.MD",
                    lambda t: transform_feature_status(t, "1.1.1", FeatureStatus.in_progress),
                    "Start login", actor)
    assert res.changed and res.branch == "main" and res.attempts == 1
    assert "| 1.1.1 | Login | In-Progress |" in remote_file(origin, "main")
    assert run_git(origin, ["log", "-1", "--format=%an <%ae>|%cn", "main"]) == \
        "Ada Lovelace <ada@example.com>|strategy-as-code"
    assert reg.get("demo").status.pushes == 1


def test_noop_edit_makes_no_commit(tmp_path, origin):
    reg = make_registry(tmp_path, origin)
    before = run_git(origin, ["rev-parse", "main"])
    res = edit_file(reg, "demo", "PRODUCT.MD", lambda t: t, "nothing")
    assert not res.changed
    assert run_git(origin, ["rev-parse", "main"]) == before


def test_retries_on_a_fresh_checkout_when_the_push_loses_a_race(tmp_path, origin):
    reg = make_registry(tmp_path, origin)
    other = clone_of(tmp_path, origin)
    calls = []

    def fn(checkout: Path):
        calls.append(1)
        if len(calls) == 1:  # someone else pushes after our fetch, before our push
            f = other / "PRODUCT.MD"
            f.write_text(f.read_text().replace("| 5 | 2 | second", "| 5 | 2 | edited elsewhere"))
            _git(other, "commit", "-qam", "concurrent edit")
            _git(other, "push", "-q", "origin", "main")
        t = (checkout / "PRODUCT.MD").read_text()
        (checkout / "PRODUCT.MD").write_text(transform_feature_status(t, "1.1.1", FeatureStatus.live))

    res = edit(reg, "demo", fn, "Mark live")
    assert res.attempts == 2 and len(calls) == 2
    final = remote_file(origin, "main")
    assert "| 1.1.1 | Login | Live |" in final   # our change
    assert "edited elsewhere" in final           # theirs survived: applied to current content


def test_gives_up_after_max_attempts(tmp_path, origin):
    reg = make_registry(tmp_path, origin)
    other = clone_of(tmp_path, origin)
    n = [0]

    def fn(checkout: Path):
        n[0] += 1
        (other / "bump.txt").write_text(str(n[0]))
        _git(other, "add", "-A")
        _git(other, "commit", "-qm", f"bump {n[0]}")
        _git(other, "pull", "-q", "--rebase", "origin", "main")
        _git(other, "push", "-q", "origin", "main")
        (checkout / "PRODUCT.MD").write_text("changed\n")

    with pytest.raises(WriteConflict):
        edit(reg, "demo", fn, "doomed", max_attempts=2)
    assert n[0] == 2
    assert reg.get("demo").status.last_ok is False


def test_failing_fn_leaves_remote_untouched_and_next_write_works(tmp_path, origin):
    reg = make_registry(tmp_path, origin)
    before = run_git(origin, ["rev-parse", "main"])

    def boom(checkout: Path):
        (checkout / "PRODUCT.MD").write_text("half-written")
        raise ValueError("nope")

    with pytest.raises(ValueError):
        edit(reg, "demo", boom, "boom")
    assert run_git(origin, ["rev-parse", "main"]) == before
    assert edit_file(reg, "demo", "PRODUCT.MD", live("1.1.2"), "ok").changed
    assert "half-written" not in remote_file(origin, "main")


def test_missing_file_is_a_write_error(tmp_path, origin):
    reg = make_registry(tmp_path, origin)
    with pytest.raises(WriteError):
        edit_file(reg, "demo", "BUGS.MD", lambda t: t, "x")


def test_branch_mode_writes_planning_branch_and_leaves_main(tmp_path, origin):
    reg = make_registry(tmp_path, origin, mode="branch")
    main_before = run_git(origin, ["rev-parse", "main"])
    res = edit_file(reg, "demo", "PRODUCT.MD", live("1.1.1"), "planning edit")
    assert res.branch == "planning"
    assert run_git(origin, ["rev-parse", "main"]) == main_before
    assert "| 1.1.1 | Login | Live |" in remote_file(origin, "planning")


def test_branch_mode_merges_new_main_commits_before_editing(tmp_path, origin):
    reg = make_registry(tmp_path, origin, mode="branch")
    edit_file(reg, "demo", "PRODUCT.MD", live("1.1.1"), "one")
    other = clone_of(tmp_path, origin)
    (other / "README.MD").write_text("human edit on main\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "human")
    _git(other, "push", "-q", "origin", "main")
    edit_file(reg, "demo", "PRODUCT.MD", live("1.1.2"), "two")
    assert remote_file(origin, "planning", "README.MD") == "human edit on main"
    assert "| 1.1.2 | Logout | Live |" in remote_file(origin, "planning")


def test_branch_mode_unmergeable_main_raises_and_keeps_branch(tmp_path, origin):
    reg = make_registry(tmp_path, origin, mode="branch")
    edit_file(reg, "demo", "PRODUCT.MD", live("1.1.1"), "one")
    planning_tip = run_git(origin, ["rev-parse", "planning"])
    other = clone_of(tmp_path, origin)
    f = other / "PRODUCT.MD"
    f.write_text(f.read_text().replace("| 1.1.1 | Login | Idea |", "| 1.1.1 | Login | Released |"))
    _git(other, "commit", "-qam", "conflicting")
    _git(other, "push", "-q", "origin", "main")
    with pytest.raises(RegistryError, match="cannot merge"):
        edit_file(reg, "demo", "PRODUCT.MD", live("1.1.2"), "two")
    assert run_git(origin, ["rev-parse", "planning"]) == planning_tip


def test_registry_refuses_a_cache_pointing_at_a_different_repo(tmp_path, origin):
    make_registry(tmp_path, origin).ensure_clone("demo")
    moved = Registry([ProjectSpec(key="demo", repo=str(tmp_path / "elsewhere.git"))], tmp_path / "cache")
    with pytest.raises(RegistryError, match="cache clone points"):
        moved.ensure_clone("demo")


def test_refresh_isolates_failures_per_project(tmp_path, origin):
    reg = Registry([ProjectSpec(key="good", repo=str(origin)),
                    ProjectSpec(key="bad", repo=str(tmp_path / "missing.git"))], tmp_path / "cache")
    reg.refresh("good")
    with pytest.raises(Exception):
        reg.refresh("bad")
    assert reg.status()["good"].last_ok is True and reg.status()["good"].sha
    assert reg.status()["bad"].sha is None


# ── manifest ──────────────────────────────────────────────────────────────

MANIFEST = """\
# Program

## Projects

| Key | Repo / Path | Role | Depends On | Mode |
| --- | ----------- | ---- | ---------- | ---- |
| accounts | git@github.com:org/accounts.git | Data | | direct |
| renewals | ../renewals | Consumer | accounts | branch |

<!-- | funnel | ~/x | | | direct | -->

## Initiatives
"""


def test_parse_manifest(tmp_path):
    specs = parse_manifest(MANIFEST, tmp_path / "program")
    assert [s.key for s in specs] == ["accounts", "renewals"]
    assert specs[0].repo == "git@github.com:org/accounts.git"
    assert specs[1].repo == str((tmp_path / "renewals").resolve())
    assert specs[1].depends_on == ("accounts",) and specs[1].mode == "branch"


@pytest.mark.parametrize("text,msg", [
    ("## Other\n", "no '## Projects'"),
    (MANIFEST.replace("| renewals |", "| accounts |"), "duplicate"),
    (MANIFEST.replace("Consumer | accounts", "Consumer | nope"), "unknown project"),
    (MANIFEST.replace("| branch |", "| sideways |"), "unknown mode"),
    (MANIFEST.replace("| Data | |", "| Data | renewals |"), "cycle"),
])
def test_manifest_errors(tmp_path, text, msg):
    with pytest.raises(ManifestError, match=msg):
        parse_manifest(text, tmp_path)
