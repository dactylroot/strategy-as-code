"""Retiring a sub-area (or area), or moving some features, carries the features, their references and the
scope summary along; nothing is written when the request is refused."""
import pytest

from app import structure_check, wbs_merge
from app.parsers import product as P

DOC = """\
# Demo

## Product Scope

### Core
- Sign in and out (WBS 1.1)
- ~~See the numbers (WBS 1.2)~~

### Ops
- Ship it (WBS 2.1)

## Features

### 1. Core

#### 1.1 Auth

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.1.1 | Login | Live | 8 | 3 | ok |
| 1.1.2 | Logout | Planned | | | |

#### 1.2 Dashboard

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.2.1 | Overview | Idea | | | see 1.1.1 |

### 2. Ops

#### 2.1 Deploy

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 2.1.1 | Pipeline | Live | | | |

---

## Known Gaps for Team Discussion

### Gap
Text.
"""

ABOUT = """\
# About

## Initiatives

### Faster entry
- 1.1.1
- 1.2.1
- other:1.2.1
"""

BUGS = """\
# Bugs

## Active

| ID | Title | Severity | Status | Notes | WBS | Fix Version |
| -- | ----- | -------- | ------ | ----- | --- | ----------- |
| 1 | Crash | High | Open | | 1.2.1 | |

## Closed
"""


def shape(text):
    return [(s.wbs_prefix, [f.wbs for f in s.features]) for a in P._parse_text(text).wbs_areas for s in a.sub_areas]


def test_merge_into_a_sibling_renumbers_and_removes_the_heading():
    r = wbs_merge.merge_sub_area(DOC, ABOUT, BUGS, "1.1", "1.2")
    assert r.mapping == {"1.1.1": "1.2.2", "1.1.2": "1.2.3"}
    assert shape(r.product) == [("1.2", ["1.2.1", "1.2.2", "1.2.3"]), ("2.1", ["2.1.1"])]
    assert "#### 1.1 " not in r.product


def test_merge_into_another_area():
    r = wbs_merge.merge_sub_area(DOC, ABOUT, BUGS, "1.2", "2.1")
    assert r.mapping == {"1.2.1": "2.1.2"}
    assert shape(r.product) == [("1.1", ["1.1.1", "1.1.2"]), ("2.1", ["2.1.1", "2.1.2"])]


def test_status_and_notes_travel_with_the_feature():
    r = wbs_merge.merge_sub_area(DOC, ABOUT, BUGS, "1.1", "2.1")
    moved = {f.wbs: f for f in P._parse_text(r.product).all_features}
    assert moved["2.1.2"].name == "Login" and moved["2.1.2"].status.value == "Live" and moved["2.1.2"].notes == "ok"
    assert moved["2.1.3"].status.value == "Planned"


def test_references_in_initiatives_and_bugs_follow_but_other_projects_do_not():
    r = wbs_merge.merge_sub_area(DOC, ABOUT, BUGS, "1.2", "2.1")
    assert "- 2.1.2" in r.about and "- 1.2.1" not in r.about
    assert "- other:1.2.1" in r.about
    assert "2.1.2" in r.bugs and "1.2.1" not in r.bugs
    assert (r.initiatives_updated, r.bugs_updated) == (1, 1)


def test_the_scope_item_is_dropped_and_the_check_stays_clean_of_dangling_items():
    r = wbs_merge.merge_sub_area(DOC, ABOUT, BUGS, "1.2", "2.1")
    assert "(WBS 1.2)" not in r.product and r.scope_item == "dropped"
    assert not any("1.2" in m and "points at" in m for m in structure_check.check(P._parse_text(r.product)))


def test_fold_scope_appends_the_text_to_the_target_item():
    r = wbs_merge.merge_sub_area(DOC, ABOUT, BUGS, "1.2", "1.1", fold_scope=True)
    assert "- Sign in and out; See the numbers (WBS 1.1)\n\n### Ops" in r.product and r.scope_item == "folded"


def test_merging_an_empty_sub_area_just_removes_it():
    doc = DOC.replace("| 2.1.1 | Pipeline | Live | | | |\n", "")
    r = wbs_merge.merge_sub_area(doc, ABOUT, BUGS, "2.1", "1.1")
    assert r.mapping == {} and "#### 2.1 " not in r.product


@pytest.mark.parametrize("source,target,msg", [
    ("1.1", "1.1", "itself"), ("1.1", "9.9", "not found"), ("7.7", "1.1", "not found"), ("x", "1.1", "looks like"),
])
def test_refusals(source, target, msg):
    with pytest.raises(ValueError, match=msg):
        wbs_merge.merge_sub_area(DOC, ABOUT, BUGS, source, target)


def test_move_a_subset_and_refuse_a_move_into_its_own_sub_area():
    r = wbs_merge.move_features(DOC, ABOUT, BUGS, ["1.1.2"], "2.1")
    assert shape(r.product)[0] == ("1.1", ["1.1.1"]) and shape(r.product)[2] == ("2.1", ["2.1.1", "2.1.2"])
    with pytest.raises(ValueError, match="already in"):
        wbs_merge.move_features(DOC, ABOUT, BUGS, ["1.1.2"], "1.1")
    with pytest.raises(ValueError, match="not found"):
        wbs_merge.move_features(DOC, ABOUT, BUGS, ["1.1.9"], "2.1")


def test_merge_area_moves_every_sub_area_and_drops_the_area_and_its_scope_group():
    r = wbs_merge.merge_area(DOC, ABOUT, BUGS, 2, "1.2")
    assert shape(r.product) == [("1.1", ["1.1.1", "1.1.2"]), ("1.2", ["1.2.1", "1.2.2"])]
    assert "### 2. Ops" not in r.product and "### Ops" not in r.product
    assert r.mapping == {"2.1.1": "1.2.2"}
    # the only drift left is real: 1.2's scope item is ticked as shipped but it now holds a Live feature + an Idea
    assert all("not a WBS area" not in m and "points at" not in m for m in structure_check.check(P._parse_text(r.product)))


def test_merge_a_first_area_warns_that_numbering_now_has_a_gap():
    r = wbs_merge.merge_area(DOC, ABOUT, BUGS, 1, "2.1")
    assert shape(r.product) == [("2.1", ["2.1.1", "2.1.2", "2.1.3", "2.1.4"])]
    assert len(r.mapping) == 3 and "- 2.1.4" in r.about
    assert any("no longer numbered" in n for n in r.notes)


def test_merge_area_refuses_a_target_inside_the_area():
    with pytest.raises(ValueError, match="inside area"):
        wbs_merge.merge_area(DOC, ABOUT, BUGS, 1, "1.1")


def test_the_input_text_is_not_mutated_and_dry_run_is_just_the_result():
    before = (DOC, ABOUT, BUGS)
    wbs_merge.merge_sub_area(DOC, ABOUT, BUGS, "1.1", "2.1")
    assert (DOC, ABOUT, BUGS) == before
