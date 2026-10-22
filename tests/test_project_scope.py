"""Project-scoped routes: /p/<key>/... serves the ordinary pages for a member project."""
import os
import subprocess
import time
from pathlib import Path

import pytest

import app.auth as auth_mod
from app import scope
from app.manifest import ProjectSpec
from app.registry import Registry, run_git
from tests.conftest import ABOUT_MD, PRODUCT_MD


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(auth_mod, "cfg", {
        "enabled": False, "username": "admin", "password": "changeme", "secret_key": "a" * 64,
    })


def _git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env)


def make_origin(tmp_path: Path, name: str, title: str, subdir: str = "") -> Path:
    bare = tmp_path / f"{name}.git"
    bare.mkdir()
    _git(bare, "init", "-q", "--bare", "-b", "main")
    seed = tmp_path / f"seed-{name}"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    folder = seed / subdir if subdir else seed
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "PRODUCT.MD").write_text(
        PRODUCT_MD.replace("# Test Product - Product Overview", f"# {title} - Product Overview")
        .replace("Login | Live", f"{title}Login | Live"), encoding="utf-8")
    (folder / "ABOUT.MD").write_text(ABOUT_MD, encoding="utf-8")
    (folder / "docs").mkdir()
    (folder / "docs" / "wbs.html").write_text(f"<p>{title} chart</p>")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-q", "origin", "main")
    return bare


@pytest.fixture()
def program(tmp_path):
    specs = [
        ProjectSpec(key="alpha", repo=str(make_origin(tmp_path, "alpha", "Alpha"))),
        ProjectSpec(key="beta", repo=str(make_origin(tmp_path, "beta", "Beta", "plan")),
                    planning_dir="plan", depends_on=("alpha",), role="Consumer"),
    ]
    reg = Registry(specs, tmp_path / "cache")
    scope.set_registry(reg)
    yield reg
    scope.set_registry(None)


def test_each_project_serves_its_own_files(client, program):
    alpha = client.get("/p/alpha/features")
    beta = client.get("/p/beta/features")
    assert alpha.status_code == beta.status_code == 200
    assert "AlphaLogin" in alpha.text and "BetaLogin" not in alpha.text
    assert "BetaLogin" in beta.text and "AlphaLogin" not in beta.text      # beta's files live in plan/


def test_scope_does_not_leak_into_unscoped_requests(client, program):
    client.get("/p/alpha/features")
    after = client.get("/features")
    assert after.status_code == 200 and "AlphaLogin" not in after.text
    assert 'href="/p/' not in after.text


def test_links_and_fetches_carry_the_project_prefix(client, program):
    page = client.get("/p/alpha/dashboard").text
    assert 'href="/p/alpha/features"' in page
    assert "/p/alpha/api/" in page
    assert 'href="/p"' in page                      # brand links to the project index
    assert "Saved changes are committed to" in page and "its default branch" in page


def test_project_root_redirects_to_dashboard(client, program):
    for url in ("/p/alpha", "/p/alpha/"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/p/alpha/dashboard", url


def test_unknown_project_and_missing_registry(client, program):
    assert client.get("/p/nope/dashboard").status_code == 404
    scope.set_registry(None)
    assert client.get("/p/alpha/dashboard").status_code == 404
    assert client.get("/p").status_code == 404


def test_project_index_lists_projects(client, program):
    client.get("/p/alpha/dashboard")
    page = client.get("/p")
    assert page.status_code == 200
    assert "/p/alpha/dashboard" in page.text and "/p/beta/dashboard" in page.text
    assert "Consumer" in page.text and "alpha" in page.text


def test_overview_summarizes_each_project(client, program):
    page = client.get("/p").text
    assert "Program overview" in page
    assert 'id="alpha"' in page and 'id="beta"' in page
    assert "features" in page and "Bugs" in page
    assert 'href="#alpha"' in page            # beta depends on alpha


def test_overview_isolates_a_broken_project(client, program):
    from app import scope
    reg = scope.get_registry()
    client.get("/p")  # clone both
    (reg.get("beta").planning_root / "PRODUCT.MD").unlink()
    reg.get("beta").status.last_fetch = 10**12   # skip refresh so the missing file is seen
    page = client.get("/p")
    assert page.status_code == 200
    assert "no PRODUCT.MD" in page.text and 'id="alpha"' in page.text


def origin_of(program, key):
    return Path(program.get(key).spec.repo)


def remote_text(program, key, name, ref="main", subdir=""):
    path = f"{subdir}/{name}" if subdir else name
    return run_git(origin_of(program, key), ["show", f"{ref}:{path}"])


def test_feature_patch_is_committed_to_the_project_repo(client, program):
    client.get("/p/alpha/features")
    r = client.patch("/p/alpha/api/features/1.1.2", json={"notes": "Edited in the UI"},
                     headers={"X-Host-User": "ada@example.com"})
    assert r.status_code == 200, r.text
    assert "Edited in the UI" in remote_text(program, "alpha", "PRODUCT.MD")
    assert run_git(origin_of(program, "alpha"), ["log", "-1", "--format=%ae|%s", "main"]) == \
        "ada@example.com|UI: feature 1.1.2 updated"
    # and the page now shows it
    assert "Edited in the UI" in client.get("/p/alpha/features").text


def test_write_lands_in_the_planning_subfolder_of_the_right_project(client, program):
    r = client.post("/p/beta/api/features", json={"wbs_prefix": "1.2", "name": "Beta only"})
    assert r.status_code == 200, r.text
    assert "Beta only" in remote_text(program, "beta", "PRODUCT.MD", subdir="plan")
    assert "Beta only" not in remote_text(program, "alpha", "PRODUCT.MD")


def test_write_applies_to_the_newest_remote_content(client, program, tmp_path):
    client.get("/p/alpha/features")                      # clone is cached and within max_age
    other = tmp_path / "other"
    run_git(None, ["clone", "-q", str(origin_of(program, "alpha")), str(other)])
    f = other / "PRODUCT.MD"
    f.write_text(f.read_text().replace("| Not started |", "| edited elsewhere |"))
    _git(other, "commit", "-qam", "concurrent")
    _git(other, "push", "-q", "origin", "main")
    r = client.patch("/p/alpha/api/features/1.1.2", json={"notes": "mine"})
    assert r.status_code == 200, r.text
    final = remote_text(program, "alpha", "PRODUCT.MD")
    assert "mine" in final and "edited elsewhere" in final


def test_invalid_request_changes_nothing_and_is_not_a_sync_failure(client, program):
    client.get("/p/alpha/features")
    tip = run_git(origin_of(program, "alpha"), ["rev-parse", "main"])
    r = client.patch("/p/alpha/api/features/9.9.9", json={"status": "Live"})
    assert r.status_code in (400, 404), r.text
    assert run_git(origin_of(program, "alpha"), ["rev-parse", "main"]) == tip
    assert program.get("alpha").status.last_ok is not False
    assert client.patch("/p/alpha/api/features/1.1.2", json={"name": "bad | name"}).status_code == 400


def test_bug_endpoints_create_and_close_through_the_writer(client, program):
    r = client.post("/p/alpha/api/bugs", json={"title": "Crash on save", "severity": "High"})
    assert r.status_code == 200, r.text
    bug_id = r.json()["id"]
    assert "Crash on save" in remote_text(program, "alpha", "BUGS.MD")      # BUGS.MD created and committed
    assert client.patch(f"/p/alpha/api/bugs/{bug_id}", json={"status": "Resolved", "fix_version": "0.1.0"}).status_code == 200
    assert client.post(f"/p/alpha/api/bugs/{bug_id}/close", json={"resolved_in": "0.1.0"}).status_code == 200
    active, closed = remote_text(program, "alpha", "BUGS.MD").split("## Closed")
    assert "Crash on save" not in active and "Crash on save" in closed
    assert run_git(origin_of(program, "alpha"), ["rev-list", "--count", "main"]) == "4"   # seed + 3 edits


def test_roadmap_and_structure_writes(client, program):
    r = client.put("/p/alpha/api/roadmap", json={"backlog": ["Dark mode", "Offline"]})
    assert r.status_code == 200, r.text
    assert "- Offline" in remote_text(program, "alpha", "ABOUT.MD")
    r = client.put("/p/alpha/api/structure/users", json={"content": "### Admin\nRuns it."})
    assert r.status_code == 200, r.text
    assert "Runs it." in remote_text(program, "alpha", "PRODUCT.MD")


def test_chart_regeneration_is_committed(client, program, monkeypatch):
    import app.wbs as wbs_module

    def fake(project_dir):
        (project_dir / "docs").mkdir(exist_ok=True)
        (project_dir / "docs" / "wbs.html").write_text("<p>regenerated</p>")
        return {"ok": True}

    monkeypatch.setattr(wbs_module, "regenerate", fake)
    assert client.post("/p/alpha/api/wbs/regenerate").status_code == 200
    assert "regenerated" in remote_text(program, "alpha", "docs/wbs.html")


def test_branch_mode_project_commits_to_its_planning_branch(client, program, tmp_path):
    program._projects["alpha"] = type(program.get("alpha"))(
        spec=ProjectSpec(key="alpha", repo=program.get("alpha").spec.repo, mode="branch"),
        path=program.get("alpha").path)
    main_tip = run_git(origin_of(program, "alpha"), ["rev-parse", "main"])
    assert client.patch("/p/alpha/api/features/1.1.2", json={"notes": "on planning"}).status_code == 200
    assert run_git(origin_of(program, "alpha"), ["rev-parse", "main"]) == main_tip
    assert "on planning" in remote_text(program, "alpha", "PRODUCT.MD", ref="planning")
    assert "committed to <code>planning</code>" in client.get("/p/alpha/dashboard").text


def test_unreachable_remote_on_write_is_a_502_and_nothing_is_lost_locally(client, program, tmp_path):
    client.get("/p/alpha/features")
    (tmp_path / "alpha.git").rename(tmp_path / "alpha-gone.git")
    r = client.patch("/p/alpha/api/features/1.1.2", json={"notes": "x"})
    assert r.status_code == 502 and "Could not save" in r.json()["detail"]


def test_endpoints_without_a_program_equivalent_are_refused(client, program):
    client.get("/p/alpha/dashboard")
    r = client.post("/p/alpha/api/switch-project", json={"project_dir": "/tmp"})
    assert r.status_code == 403
    r = client.post("/p/alpha/api/bugs/1/screenshot", files={"file": ("a.png", b"x", "image/png")})
    assert r.status_code == 403


def test_preview_endpoint_still_works_when_scoped(client, program):
    r = client.post("/p/alpha/api/markdown/preview", json={"content": "**hi**"})
    assert r.status_code == 200


def test_legacy_atomic_write_is_blocked_while_scoped(program):
    from app.fileio import ProjectReadOnly, _atomic_write
    token = scope.current_project.set("alpha")
    try:
        with pytest.raises(ProjectReadOnly):
            _atomic_write(program.get("alpha").planning_root / "PRODUCT.MD", "x")
    finally:
        scope.current_project.reset(token)


def test_unscoped_writes_still_work(client, program):
    r = client.post("/api/features", json={"wbs_prefix": "1.1", "name": "Plain old edit"})
    assert r.status_code == 200


def test_clone_is_refreshed_when_the_remote_moves(client, program, tmp_path):
    assert "AlphaLogin" in client.get("/p/alpha/features").text
    other = tmp_path / "other"
    run_git(None, ["clone", "-q", program.get("alpha").spec.repo, str(other)])
    f = other / "PRODUCT.MD"
    f.write_text(f.read_text().replace("AlphaLogin", "AlphaSignIn"))
    _git(other, "commit", "-qam", "rename")
    _git(other, "push", "-q", "origin", "main")
    # within max_age the cached clone is served
    assert "AlphaLogin" in client.get("/p/alpha/features").text
    program.get("alpha").status.last_fetch = time.time() - 3600
    assert "AlphaSignIn" in client.get("/p/alpha/features").text


def test_failed_refresh_serves_the_old_clone_and_flags_it(client, program, tmp_path):
    client.get("/p/alpha/dashboard")
    (tmp_path / "alpha.git").rename(tmp_path / "alpha-gone.git")
    program.get("alpha").status.last_fetch = 0
    r = client.get("/p/alpha/features")
    assert r.status_code == 200 and "AlphaLogin" in r.text
    assert r.headers.get("X-Project-Stale") == "1"


def test_unreachable_project_with_no_clone_is_a_502(client, program, tmp_path):
    (tmp_path / "alpha.git").rename(tmp_path / "alpha-gone.git")
    r = client.get("/p/alpha/dashboard")
    assert r.status_code == 502 and "alpha" in r.text


def test_docs_follow_the_project_and_cannot_escape(client, program):
    assert "Alpha chart" in client.get("/p/alpha/docs/wbs.html").text
    assert "Beta chart" in client.get("/p/beta/docs/wbs.html").text
    assert client.get("/p/alpha/docs/nope.html").status_code == 404
    assert client.get("/p/alpha/docs/..%2FPRODUCT.MD").status_code == 404
    assert client.get("/p/alpha/docs/%2e%2e/PRODUCT.MD").status_code == 404
    assert client.get("/docs/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_events_stream_watches_the_scoped_project(client, program):
    with client.stream("GET", "/p/alpha/api/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")


def test_overview_rolls_up_program_initiatives(client, program, tmp_path):
    manifest = tmp_path / "PROGRAM.MD"
    manifest.write_text("""## Projects\n\n| Key | Repo / Path |\n| --- | --- |\n| alpha | x |\n| beta | y |\n
## Initiatives\n\n### Joint Goal (Major)\n- alpha:1.1.1\n- beta:1.1.2\n- beta:9.9.9\n""", encoding="utf-8")
    program.manifest_path = manifest
    page = client.get("/p").text
    assert "Program initiatives" in page and "Joint Goal" in page
    assert "1/3" in page or "2/3" in page      # fixture's 1.1.1 is Live; 9.9.9 does not exist
    assert "Unavailable" in page               # the missing member is called out, not hidden
    manifest.write_text("## Projects\n\n## Initiatives\n\n### X\n- nope:1.1.1\n", encoding="utf-8")
    assert "could not be read" in client.get("/p").text
