from datetime import datetime, timedelta, timezone

import pytest

from app import inbox
from app.cli import main
from app.registry import run_git
from tests.test_cli import manifest, remote  # noqa: F401
from tests.test_registry_writer import origin  # noqa: F401


def ls(origin, path="plan/.planning/inbox"):
    out = run_git(origin, ["ls-tree", "--name-only", "main", path + "/"])
    return [l.rsplit("/", 1)[-1] for l in out.splitlines()]


def submit(manifest, *args):
    assert main(["--manifest", str(manifest), "inbox", "submit", *args]) == 0


def test_write_item_validates_and_never_writes_pipes_into_tables(tmp_path):
    with pytest.raises(inbox.InboxError):
        inbox.write_item(tmp_path, "bug", "   ", "", "u")
    with pytest.raises(inbox.InboxError):
        inbox.write_item(tmp_path, "idea", "t", "", "u")
    with pytest.raises(inbox.InboxError):
        inbox.write_item(tmp_path, "bug", "t", "x" * (inbox.MAX_BODY + 1), "u")
    item = inbox.write_item(tmp_path, "bug", "Line one\nline two", "body", "ada", severity="HIGH")
    assert "\n" not in item.title and item.severity == "high"


def test_submit_creates_one_file_per_item_and_list_shows_them(manifest, origin, capsys):
    submit(manifest, "bug", "Export fails", "--body", "It crashes", "--severity", "high")
    submit(manifest, "feature", "Dark mode")
    assert len(ls(origin)) == 2
    assert main(["--manifest", str(manifest), "inbox", "list"]) == 0
    out = capsys.readouterr().out
    assert "Export fails" in out and "Dark mode" in out
    assert "inbox: bug submitted: Export fails" in run_git(origin, ["log", "--format=%s", "main"])


def test_promote_bug_is_one_commit_that_adds_the_row_and_removes_the_file(manifest, origin, capsys):
    submit(manifest, "bug", "Export fails", "--body", "a | b", "--severity", "high", "--wbs", "1.1.1")
    item_id = ls(origin)[0].removesuffix(".md")
    before = run_git(origin, ["rev-list", "--count", "main"])
    assert main(["--manifest", str(manifest), "inbox", "promote", item_id]) == 0
    assert int(run_git(origin, ["rev-list", "--count", "main"])) == int(before) + 1
    bugs = remote(origin, "BUGS.MD")
    assert "| 4 | Export fails | High | Open |" in bugs and f"(inbox {item_id})" in bugs and "a / b" in bugs
    assert ls(origin) == []


def test_promote_feature_needs_a_prefix_then_adds_the_next_wbs(manifest, origin, capsys):
    submit(manifest, "feature", "Dark mode")
    item_id = ls(origin)[0].removesuffix(".md")
    assert main(["--manifest", str(manifest), "inbox", "promote", item_id]) == 1
    assert "sub-area" in capsys.readouterr().err and ls(origin)
    assert main(["--manifest", str(manifest), "inbox", "promote", item_id, "--prefix", "1.2"]) == 0
    assert "| 1.2.2 | Dark mode |" in remote(origin, "PRODUCT.MD")


def test_retried_promotion_only_removes_the_file(tmp_path):
    (tmp_path / "BUGS.MD").write_text(
        "# Bugs\n\n## Active\n\n| ID | Title | Severity | Status | Notes | WBS | Fix Version | Owner | UAT Confirmed | GH Issue |\n"
        "|----|-------|----------|--------|-------|-----|-------------|-------|----------------|----------|\n\n## Closed\n")
    item = inbox.write_item(tmp_path, "bug", "Crash", "boom", "u")
    path = tmp_path / inbox.INBOX / f"{item.id}.md"
    saved = path.read_text()
    inbox.promote(tmp_path, item.id)
    path.write_text(saved)                       # the file reappears, e.g. the commit was lost after the row landed
    assert inbox.promote(tmp_path, item.id).startswith("already promoted")
    assert (tmp_path / "BUGS.MD").read_text().count(f"(inbox {item.id})") == 1
    assert not path.exists()


def test_decline_and_duplicate_record_the_reason_in_the_commit(manifest, origin):
    submit(manifest, "bug", "Spam")
    submit(manifest, "bug", "Again")
    a, b = [n.removesuffix(".md") for n in ls(origin)]
    assert main(["--manifest", str(manifest), "inbox", "decline", a, "--reason", "not a bug"]) == 0
    assert main(["--manifest", str(manifest), "inbox", "duplicate", b, "--of", "2"]) == 0
    log = run_git(origin, ["log", "--format=%s", "main"])
    assert "not a bug" in log and f"duplicate {b} of 2" in log and ls(origin) == []


def test_stale_flag_and_archive_sweep(tmp_path):
    old = datetime.now(timezone.utc) - timedelta(days=15)
    older = datetime.now(timezone.utc) - timedelta(days=61)
    inbox.write_item(tmp_path, "bug", "Fresh", "", "u")
    inbox.write_item(tmp_path, "bug", "Old", "", "u", now=old)
    inbox.write_item(tmp_path, "bug", "Ancient", "", "u", now=older)
    by_title = {i.title: i for i in inbox.list_items(tmp_path)}
    assert not by_title["Fresh"].stale and by_title["Old"].stale and by_title["Ancient"].stale
    moved = inbox.sweep(tmp_path)
    assert moved == [by_title["Ancient"].id]
    assert {i.title for i in inbox.list_items(tmp_path)} == {"Fresh", "Old"}
    assert [i.title for i in inbox.list_items(tmp_path, include_archive=True) if i.archived] == ["Ancient"]


def test_screenshot_is_renamed_on_promotion(tmp_path):
    (tmp_path / "BUGS.MD").write_text(
        "# Bugs\n\n## Active\n\n| ID | Title | Severity | Status | Notes | WBS | Fix Version | Owner | UAT Confirmed | GH Issue |\n"
        "|----|-------|----------|--------|-------|-----|-------------|-------|----------------|----------|\n\n## Closed\n")
    item = inbox.write_item(tmp_path, "bug", "Crash", "", "u")
    (tmp_path / inbox.INBOX / f"{item.id}.png").write_bytes(b"png")
    inbox.promote(tmp_path, item.id)
    assert (tmp_path / ".screenshots" / "bug_1.png").read_bytes() == b"png"
    assert not list((tmp_path / inbox.INBOX).glob("*"))
