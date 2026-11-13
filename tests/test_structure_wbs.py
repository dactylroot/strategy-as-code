"""The first two WBS levels are defined from the Structure page: areas and sub-areas are renamed, reordered,
added and removed without touching a feature row or changing any code."""
import pytest

from app.parsers import product as P

DOC = """\
# Demo

## Product Scope

### Core
- Something

## Features

Codes follow `{area}.{sub-area}.{feature}`.

### 1. Core

#### 1.1 Auth

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.1.1 | Login | Live | 8 | 3 | ok |

#### 1.2 Dashboard

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.2.1 | Overview | Idea | | | |

### 2. Ops

#### 2.1 Deploy

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |

---

## Known Gaps for Team Discussion

### Gap
Text.
"""


def shape(text):
    return [(a.number, a.title, [(s.wbs_prefix, s.title, [f.wbs for f in s.features]) for s in a.sub_areas])
            for a in P._parse_text(text).wbs_areas]


def keep(areas=None, **kw):
    return [{"number": a.number, "title": a.title, "sub_areas": [{"prefix": s.wbs_prefix, "title": s.title} for s in a.sub_areas]}
            for a in P._parse_text(DOC).wbs_areas]


def test_an_unchanged_structure_leaves_the_file_as_it_was():
    assert P.transform_structure(DOC, keep()) == DOC


def test_rename_reorder_and_add_keep_every_code_and_feature():
    s = keep()
    s[0]["title"] = "Core Platform"
    s[0]["sub_areas"].reverse()                                   # 1.2 before 1.1; codes stay
    s[0]["sub_areas"].append({"prefix": None, "title": "Reports"})
    s.append({"number": None, "title": "Data", "sub_areas": [{"prefix": None, "title": "Ingest"}]})
    s.reverse()
    out = P.transform_structure(DOC, s)
    assert shape(out) == [
        (3, "Data", [("3.1", "Ingest", [])]),
        (2, "Ops", [("2.1", "Deploy", [])]),
        (1, "Core Platform", [("1.2", "Dashboard", ["1.2.1"]), ("1.1", "Auth", ["1.1.1"]), ("1.3", "Reports", [])]),
    ]
    assert "| 1.1.1 | Login | Live | 8 | 3 | ok |" in out and "Codes follow `{area}.{sub-area}.{feature}`." in out
    assert "\n---\n\n## Known Gaps" in out and "## Product Scope\n\n### Core\n- Something" in out
    # a feature can be added to the new sub-area straight away
    from app.models import NewFeature
    again, f = P.transform_add_feature(out, NewFeature(wbs_prefix="1.3", name="Weekly report"))
    assert f.wbs == "1.3.1"


def test_a_sub_area_or_area_holding_features_cannot_be_removed():
    s = keep()
    s[0]["sub_areas"].pop(0)
    with pytest.raises(ValueError, match=r"1\.1 still holds 1 feature"):
        P.transform_structure(DOC, s)
    with pytest.raises(ValueError, match=r"still holds"):
        P.transform_structure(DOC, keep()[1:])


def test_empty_ones_can_be_removed_and_numbers_are_not_reused_while_a_sibling_remains():
    s = keep()[:1] + [{"number": None, "title": "Fresh", "sub_areas": []}]
    out = P.transform_structure(DOC, s)
    assert [a[:2] for a in shape(out)] == [(1, "Core"), (3, "Fresh")]


def test_bad_requests_are_refused_and_change_nothing():
    ghost = keep() + [{"number": 9, "title": "X", "sub_areas": []}]
    with pytest.raises(ValueError, match="no longer exists"):
        P.transform_structure(DOC, ghost)
    blank = keep()
    blank[0]["title"] = "  "
    with pytest.raises(ValueError, match="needs a name"):
        P.transform_structure(DOC, blank)
    moved = keep()
    moved[1]["sub_areas"].append(moved[0]["sub_areas"].pop())
    with pytest.raises(ValueError, match="cannot move to another area"):
        P.transform_structure(DOC, moved)
    with pytest.raises(ValueError, match="listed twice"):
        P.transform_structure(DOC, keep() + keep()[:1])
