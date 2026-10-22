import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.mergedriver import merge_text

DRIVER = Path(__file__).resolve().parent.parent / "app" / "mergedriver.py"

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

BUGS = """\
# Bugs

## Active

| ID | Title | Severity | Status | Notes | WBS | Fix Version | Owner | UAT Confirmed | GH Issue |
|----|-------|----------|--------|-------|-----|-------------|-------|----------------|----------|
| 1 | Crash | High | Open | | 1.1.1 | | | | |
| 2 | Typo | Low | Investigating | | | | | | |

## Closed

| ID | Title | Notes | Resolved In | GH Issue |
|----|-------|-------|-------------|----------|
"""


def rep(text, a, b):
    assert a in text
    return text.replace(a, b)


def test_independent_field_edits_both_survive():
    ours = rep(PRODUCT, "| Idea | 8 | 3 | first", "| Idea | 9 | 3 | first")
    theirs = rep(PRODUCT, "| Idea | 8 | 3 | first", "| Idea | 8 | 3 | reworded")
    out = merge_text(PRODUCT, ours, theirs, "product")
    assert "| 1.1.1 | Login | Idea | 9 | 3 | reworded |" in out


def test_status_takes_the_more_advanced_stage_either_way():
    live = rep(PRODUCT, "| Login | Idea |", "| Login | Live |")
    prog = rep(PRODUCT, "| Login | Idea |", "| Login | In-Progress |")
    assert "| Login | Live |" in merge_text(PRODUCT, prog, live, "product")
    assert "| Login | Live |" in merge_text(PRODUCT, live, prog, "product")


def test_released_beats_live():
    rel = rep(PRODUCT, "| Login | Idea |", "| Login | Released |")
    live = rep(PRODUCT, "| Login | Idea |", "| Login | Live |")
    assert "| Login | Released |" in merge_text(PRODUCT, live, rel, "product")


def test_added_rows_from_both_sides_are_kept_and_collisions_renumbered():
    ours = PRODUCT + "| 1.1.3 | Reset | Idea | | | ours |\n"
    theirs = PRODUCT + "| 1.1.3 | Audit | Idea | | | theirs |\n"
    out = merge_text(PRODUCT, ours, theirs, "product")
    assert "| 1.1.3 | Reset |" in out and "| 1.1.4 | Audit |" in out


def test_edit_versus_delete_keeps_the_edit_and_plain_delete_deletes():
    deleted = rep(PRODUCT, "| 1.1.2 | Logout | Idea | 5 | 2 | second |\n", "")
    edited = rep(PRODUCT, "second", "second, edited")
    assert "Logout" in merge_text(PRODUCT, deleted, edited, "product")
    assert "Logout" not in merge_text(PRODUCT, deleted, PRODUCT, "product")


def test_closed_bug_wins_over_resolved():
    closed = rep(rep(BUGS, "| 1 | Crash | High | Open | | 1.1.1 | | | | |\n", ""),
                 "|-------|-------|-------------|----------|\n",
                 "|-------|-------|-------------|----------|\n| 1 | Crash | fixed | 0.2.0 |  |\n")
    resolved = rep(BUGS, "| High | Open |", "| High | Resolved |")
    out = merge_text(BUGS, closed, resolved, "bugs")
    active, closed_part = out.split("## Closed")
    assert "| 1 |" not in active and "| 1 | Crash |" in closed_part


def test_both_file_the_same_bug_id_renumbers_the_incoming_one():
    ours = BUGS.replace("| 2 | Typo", "| 3 | Mine | Low | Open | | | | | | |\n| 2 | Typo")
    theirs = BUGS.replace("| 2 | Typo", "| 3 | Theirs | Low | Open | | | | | | |\n| 2 | Typo")
    out = merge_text(BUGS, ours, theirs, "bugs")
    assert "| 3 | Mine |" in out and "| 4 | Theirs |" in out


def test_prose_conflict_is_reported():
    ours = rep(PRODUCT, "# Demo", "# Ours")
    theirs = rep(PRODUCT, "# Demo", "# Theirs")
    assert merge_text(PRODUCT, ours, theirs, "product") is None


def _git(cwd, *a):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *a], cwd=cwd, env=env, capture_output=True, text=True)


def test_git_merge_uses_the_driver(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "merge.strategy-rows.driver", f"{sys.executable} {DRIVER} %O %A %B %P")
    (r / ".gitattributes").write_text("PRODUCT.MD merge=strategy-rows\n")
    (r / "PRODUCT.MD").write_text(PRODUCT)
    _git(r, "add", "-A"); _git(r, "commit", "-qm", "base")
    _git(r, "checkout", "-qb", "dev")
    (r / "PRODUCT.MD").write_text(rep(PRODUCT, "| Login | Idea |", "| Login | Live |"))
    _git(r, "commit", "-qam", "dev")
    _git(r, "checkout", "-q", "main")
    (r / "PRODUCT.MD").write_text(rep(PRODUCT, "| Login | Idea | 8 |", "| Login | In-Progress | 9 |"))
    _git(r, "commit", "-qam", "ui")
    m = _git(r, "merge", "dev", "-m", "merge")
    assert m.returncode == 0, m.stdout + m.stderr
    assert "| 1.1.1 | Login | Live | 9 | 3 | first |" in (r / "PRODUCT.MD").read_text()
