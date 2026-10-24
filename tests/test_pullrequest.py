import json
import threading

import httpx
import pytest

from app import pullrequest as pr
from app.cli import main
from app.manifest import ProjectSpec
from app.registry import Registry, run_git
from app.writer import edit
from tests.test_cli import manifest  # noqa: F401
from tests.test_registry_writer import origin  # noqa: F401
from tests.test_project_scope import make_origin


def fake_github(open_prs=None, fail=False):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, dict(request.url.params), body))
        if fail:
            return httpx.Response(403, json={"message": "no"})
        if request.method == "GET":
            return httpx.Response(200, json=open_prs or [])
        if request.method == "POST":
            return httpx.Response(201, json={"number": 7, "html_url": "https://github.com/o/r/pull/7"})
        return httpx.Response(200, json={})

    return httpx.Client(base_url=pr.API, transport=httpx.MockTransport(handler)), calls


@pytest.fixture()
def branch_project(tmp_path):
    reg = Registry([ProjectSpec(key="alpha", repo=str(make_origin(tmp_path, "alpha", "Alpha")), mode="branch")], tmp_path / "cache")
    return reg, reg.refresh("alpha")


@pytest.fixture()
def github_project():
    return Registry([ProjectSpec(key="g", repo="https://github.com/o/r.git", mode="branch")]).get("g")


def test_github_repo_parses_https_and_ssh_urls_only():
    assert pr.github_repo("https://github.com/dactylroot/revenue-hub.git") == ("dactylroot", "revenue-hub")
    assert pr.github_repo("git@github.com:acme/plan") == ("acme", "plan")
    assert pr.github_repo("/srv/git/alpha.git") is None and pr.github_repo("https://gitlab.com/a/b.git") is None


def test_description_lists_commits_and_truncates():
    title, body = pr.describe("planning", "main", ["a (Ada)"])
    assert title == "Planning updates: 1 change" and "- a (Ada)" in body and "`planning`" in body
    title, body = pr.describe("planning", "main", [f"c{i} (x)" for i in range(40)])
    assert title.endswith("40 changes") and "- and 10 more" in body and "c30" not in body


def test_opens_a_pull_request_when_none_is_open(github_project):
    project = github_project
    client, calls = fake_github()
    made = pr.ensure_pull_request(project, "main", ["x (Ada)"], client)
    assert made == pr.PullRequest(7, "https://github.com/o/r/pull/7", True)
    assert calls[0][:3] == ("GET", "/repos/o/r/pulls", {"head": "o:planning", "base": "main", "state": "open"})
    post = calls[-1]
    assert post[0] == "POST" and post[3]["head"] == "planning" and post[3]["base"] == "main" and "x (Ada)" in post[3]["body"]


def test_refreshes_the_open_one_instead_of_opening_another(github_project):
    project = github_project
    client, calls = fake_github([{"number": 3, "html_url": "https://github.com/o/r/pull/3"}])
    made = pr.ensure_pull_request(project, "main", ["x (Ada)", "y (Bo)"], client)
    assert (made.number, made.created) == (3, False)
    assert [c[0] for c in calls] == ["GET", "PATCH"] and calls[1][1].endswith("/pulls/3") and "y (Bo)" in calls[1][3]["body"]


def test_nothing_waiting_and_nothing_open_does_nothing(github_project):
    project = github_project
    client, calls = fake_github()
    assert pr.ensure_pull_request(project, "main", [], client) is None
    assert [c[0] for c in calls] == ["GET"]


def test_an_api_failure_raises(github_project):
    project = github_project
    client, _ = fake_github(fail=True)
    with pytest.raises(httpx.HTTPStatusError):
        pr.ensure_pull_request(project, "main", ["x"], client)


def test_enabled_needs_branch_mode_token_and_a_github_repo(branch_project, monkeypatch):
    _, project = branch_project
    monkeypatch.setenv("GIT_TOKEN", "t")
    assert not pr.enabled(project)                                  # a local path
    github = Registry([ProjectSpec(key="g", repo="https://github.com/o/r.git", mode="branch")]).get("g")
    assert pr.enabled(github)
    monkeypatch.setenv("PLANNING_PR", "false")
    assert not pr.enabled(github)
    monkeypatch.delenv("PLANNING_PR"); monkeypatch.delenv("GIT_TOKEN")
    assert not pr.enabled(github)
    direct = Registry([ProjectSpec(key="d", repo="https://github.com/o/r.git")]).get("d")
    monkeypatch.setenv("GIT_TOKEN", "t")
    assert not pr.enabled(direct)


def test_a_write_refreshes_the_pull_request_with_the_commits_waiting(branch_project, monkeypatch):
    reg, project = branch_project
    seen, done = [], threading.Event()
    monkeypatch.setattr(pr, "enabled", lambda p: True)
    monkeypatch.setattr(pr, "ensure_pull_request",
                        lambda p, base, commits, client=None: (seen.append((base, commits)), done.set(),
                                                               pr.PullRequest(7, "https://github.com/o/r/pull/7", True))[2])
    edit(reg, "alpha", lambda root: (root / "NOTE.md").write_text("hi"), "one change")
    assert done.wait(5)
    assert seen[0][0] == "main" and seen[0][1] == ["one change (strategy-as-code)"]
    for _ in range(50):
        if project.status.pr_url:
            break
        threading.Event().wait(0.05)
    assert project.status.pr_url == "https://github.com/o/r/pull/7"


def test_a_pull_request_failure_never_fails_the_write(branch_project, monkeypatch):
    reg, project = branch_project
    done = threading.Event()
    monkeypatch.setattr(pr, "enabled", lambda p: True)

    def boom(*a, **k):
        done.set()
        raise httpx.ConnectError("down")
    monkeypatch.setattr(pr, "ensure_pull_request", boom)
    result = edit(reg, "alpha", lambda root: (root / "NOTE.md").write_text("hi"), "still saved")
    assert result.changed and done.wait(5)
    assert run_git(project.path, ["log", "-1", "--format=%s", "origin/planning"]) == "still saved"


def test_cli_pr_explains_what_is_missing(manifest, origin, monkeypatch, capsys):  # noqa: F811
    monkeypatch.delenv("GIT_TOKEN", raising=False)
    assert main(["--manifest", str(manifest), "pr"]) == 1
    assert "direct mode" in capsys.readouterr().err
    manifest.write_text(manifest.read_text().replace("| direct |", "| branch |"))
    assert main(["--manifest", str(manifest), "pr"]) == 1
    assert "GIT_TOKEN" in capsys.readouterr().err
    monkeypatch.setenv("GIT_TOKEN", "t")
    assert main(["--manifest", str(manifest), "pr"]) == 1
    assert "not a github.com repository" in capsys.readouterr().err
