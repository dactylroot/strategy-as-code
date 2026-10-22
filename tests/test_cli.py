import os
from pathlib import Path

import pytest

from app.cli import CliError, find_manifest, main
from app.registry import run_git
from tests.test_registry_writer import PRODUCT, _git, origin  # noqa: F401  (origin is a fixture)

BUGS = """\
# Bugs

## Active

| ID | Title | Severity | Status | Notes | WBS | Fix Version | Owner | UAT Confirmed | GH Issue |
|----|-------|----------|--------|-------|-----|-------------|-------|----------------|----------|
| 1 | Existing | Low | Open | n | | | | | |
| 2 | Tied to login | Low | Open | n | 1.1.1 | | | | |
| 3 | Tied to logout | Low | Open | n | 1.1.2 | | | | |

## Closed

| ID | Title | Notes | Resolved In | GH Issue |
|----|-------|-------|-------------|----------|
"""


ABOUT = """\
# Changelog

# Roadmap

## Initiatives

### Auth Push (Major)
- 1.1.1
- 1.1.2
- other:1.1.1

## Backlog
- Mobile
"""

MORE = """
#### 1.2 Reporting

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.2.1 | Export | Idea | | | |
"""


@pytest.fixture()
def manifest(tmp_path, origin, monkeypatch):  # noqa: F811
    # Put planning files in a subfolder of the repo, as revops/program does.
    work = tmp_path / "seed2"
    run_git(None, ["clone", "-q", str(origin), str(work)])
    (work / "plan").mkdir()
    _git(work, "mv", "PRODUCT.MD", "plan/PRODUCT.MD")
    (work / "plan" / "BUGS.MD").write_text(BUGS)
    (work / "plan" / "ABOUT.MD").write_text(ABOUT)
    product = (work / "plan" / "PRODUCT.MD")
    text = product.read_text()
    text = text.replace("| 1.1.1 | Login | Idea | 8 | 3 | first |", "| 1.1.1 | Login | Live | 8 | 3 | first | gap | Ada | Yes |")
    product.write_text(text + MORE)
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "layout")
    _git(work, "push", "-q", "origin", "main")
    m = tmp_path / "PROGRAM.MD"
    m.write_text(
        "## Projects\n\n| Key | Repo / Path | Planning Dir | Mode |\n| --- | --- | --- | --- |\n"
        f"| demo | {origin} | plan | direct |\n", encoding="utf-8")
    monkeypatch.setenv("STRATEGY_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("STRATEGY_ACTOR", "Grace Hopper <grace@example.com>")
    return m


def remote(origin, name):
    return run_git(origin, ["show", f"main:plan/{name}"])


def test_feature_status_commits_as_actor(manifest, origin, capsys):
    assert main(["--manifest", str(manifest), "feature", "status", "1.1.1", "live"]) == 0
    assert "demo: feature 1.1.1 -> Live (main @" in capsys.readouterr().out
    assert "| 1.1.1 | Login | Live |" in remote(origin, "PRODUCT.MD")
    assert run_git(origin, ["log", "-1", "--format=%an <%ae>|%s", "main"]) == \
        "Grace Hopper <grace@example.com>|feature 1.1.1 -> Live"


def test_released_requires_uat_flag(manifest, origin, capsys):
    before = run_git(origin, ["rev-parse", "main"])
    assert main(["--manifest", str(manifest), "feature", "status", "1.1.1", "released"]) == 1
    assert "--uat-approved" in capsys.readouterr().err
    assert run_git(origin, ["rev-parse", "main"]) == before
    assert main(["--manifest", str(manifest), "feature", "status", "1.1.1", "released", "--uat-approved"]) == 0


def test_notes_and_score(manifest, origin):
    assert main(["--manifest", str(manifest), "feature", "notes", "1.1.2", "Does the thing"]) == 0
    assert main(["--manifest", str(manifest), "feature", "score", "1.1.2", "9"]) == 0
    row = [l for l in remote(origin, "PRODUCT.MD").splitlines() if l.startswith("| 1.1.2 ")][0]
    assert "Does the thing" in row and "| 9 |" in row


def test_unknown_wbs_fails_cleanly(manifest, origin, capsys):
    before = run_git(origin, ["rev-parse", "main"])
    assert main(["--manifest", str(manifest), "feature", "status", "9.9.9", "live"]) == 1
    assert "9.9.9" in capsys.readouterr().err
    assert run_git(origin, ["rev-parse", "main"]) == before


def test_bug_lifecycle(manifest, origin, capsys):
    base = ["--manifest", str(manifest)]
    assert main(base + ["bug", "add", "Crash on save", "--severity", "high"]) == 0
    assert "| 4 | Crash on save | High | Open |" in remote(origin, "BUGS.MD")
    assert main(base + ["bug", "status", "4", "resolved", "--fix", "0.4.2"]) == 0
    assert "Resolved" in remote(origin, "BUGS.MD") and "0.4.2" in remote(origin, "BUGS.MD")
    assert main(base + ["bug", "close", "4", "--resolved-in", "0.4.2"]) == 0
    active, closed = remote(origin, "BUGS.MD").split("## Closed")
    assert "Crash on save" not in active and "Crash on save" in closed


def test_chart_is_off_by_default_and_opt_in(manifest, origin, tmp_path, monkeypatch):
    stub = tmp_path / "gen.py"
    stub.write_text("import os; d=os.environ['PROJECT_DIR']; open(d+'/docs/wbs.html','w').write('chart')\n")
    monkeypatch.setenv("GEN_WBS_SCRIPT", str(stub))
    base = ["--manifest", str(manifest)]
    assert main(base + ["feature", "status", "1.1.1", "live"]) == 0
    assert run_git(origin, ["ls-tree", "-r", "--name-only", "main"]).count("wbs.html") == 0
    assert main(base + ["--chart", "feature", "status", "1.1.2", "live"]) == 0
    assert "plan/docs/wbs.html" in run_git(origin, ["ls-tree", "-r", "--name-only", "main"])
    # the status change and the chart are one commit
    assert run_git(origin, ["show", "--stat", "--format=", "main"]).count("|") == 2


def test_several_projects_need_a_choice(manifest, origin, capsys):
    manifest.write_text(manifest.read_text() + f"| other | {origin} | plan | direct |\n")
    assert main(["--manifest", str(manifest), "feature", "status", "1.1.1", "live"]) == 1
    assert "--project" in capsys.readouterr().err
    assert main(["--manifest", str(manifest), "-p", "demo", "feature", "status", "1.1.1", "live"]) == 0


def test_find_manifest_walks_up_and_checks_program_subfolder(tmp_path):
    (tmp_path / "program").mkdir()
    (tmp_path / "program" / "PROGRAM.MD").write_text("x")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert find_manifest(None, deep) == tmp_path / "program" / "PROGRAM.MD"
    with pytest.raises(CliError):
        find_manifest(str(tmp_path / "nope.md"))


def test_feature_add_assigns_next_wbs_and_names_it_in_the_commit(manifest, origin, capsys):
    base = ["--manifest", str(manifest)]
    assert main(base + ["feature", "add", "1.2", "Dashboards", "--value", "7", "--notes", "charts"]) == 0
    assert "feature 1.2.2 added: Dashboards" in capsys.readouterr().out
    assert "| 1.2.2 | Dashboards | Idea | 7 |" in remote(origin, "PRODUCT.MD")
    assert run_git(origin, ["log", "-1", "--format=%s", "main"]) == "feature 1.2.2 added: Dashboards"
    assert main(base + ["feature", "add", "1.2", "More"]) == 0
    assert "| 1.2.3 | More |" in remote(origin, "PRODUCT.MD")


def test_feature_add_rejects_bad_input_and_unknown_area(manifest, origin, capsys):
    base = ["--manifest", str(manifest)]
    before = run_git(origin, ["rev-parse", "main"])
    assert main(base + ["feature", "add", "1.2", "a | b"]) == 1
    assert main(base + ["feature", "add", "1.2", "x", "--notes", "p|q"]) == 1
    assert main(base + ["feature", "add", "7.7", "x"]) == 1
    assert "7.7" in capsys.readouterr().err
    assert run_git(origin, ["rev-parse", "main"]) == before


def test_feature_move_is_one_commit_that_rewrites_references(manifest, origin, capsys):
    base = ["--manifest", str(manifest)]
    assert main(base + ["feature", "move", "1.1.1", "1.2"]) == 0
    out = capsys.readouterr().out
    assert "feature 1.1.1 moved to 1.2.2" in out
    assert "demo:1.1.1 are not updated" in out
    product = remote(origin, "PRODUCT.MD")
    row = [l for l in product.splitlines() if l.startswith("| 1.2.2 ")][0]
    assert "Login" in row and "gap" in row and "Ada" in row and "Yes" in row   # flag/owner/UAT carried
    assert "| 1.1.1 |" not in product
    about = remote(origin, "ABOUT.MD")
    assert "- 1.2.2\n- 1.1.2\n- other:1.1.1" in about                       # other project's ref untouched
    bugs = remote(origin, "BUGS.MD")
    assert "| 2 | Tied to login | Low | Open | n | 1.2.2 |" in bugs
    assert "| 3 | Tied to logout | Low | Open | n | 1.1.2 |" in bugs          # unrelated bug untouched
    assert run_git(origin, ["rev-list", "--count", "HEAD~1..main"]) == "1"      # one commit for all three files
    assert run_git(origin, ["log", "-1", "--format=%s", "main"]) == "feature 1.1.1 moved to 1.2.2"


def test_feature_move_warns_about_leftover_mentions(manifest, origin, capsys):
    base = ["--manifest", str(manifest)]
    assert main(base + ["feature", "notes", "1.1.2", "depends on 1.1.1"]) == 0
    capsys.readouterr()
    assert main(base + ["feature", "move", "1.1.1", "1.2"]) == 0
    assert "1 other mention(s) of 1.1.1" in capsys.readouterr().out


def test_feature_move_unknown_feature_changes_nothing(manifest, origin, capsys):
    before = run_git(origin, ["rev-parse", "main"])
    assert main(["--manifest", str(manifest), "feature", "move", "9.9.9", "1.2"]) == 1
    assert run_git(origin, ["rev-parse", "main"]) == before


def test_merge_driver_install_configures_git_and_attributes(tmp_path):
    import subprocess
    from app.cli import install_merge_driver
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    msg = install_merge_driver(tmp_path)
    assert "commit it" in msg
    attrs = (tmp_path / ".gitattributes").read_text()
    assert "PRODUCT.MD merge=strategy-rows" in attrs and "BUGS.MD merge=strategy-rows" in attrs
    driver = subprocess.run(["git", "config", "merge.strategy-rows.driver"], cwd=tmp_path,
                            capture_output=True, text=True).stdout
    assert "mergedriver.py" in driver and "%O %A %B %P" in driver
    assert "already set" in install_merge_driver(tmp_path)       # idempotent
