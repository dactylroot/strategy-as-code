import pytest
from app.parsers import about as parser
from app.models import (
    RoadmapUpdate, NewRelease, InitiativeUpdate,
    ProductDoc, WBSArea, WBSSubArea, Feature, FeatureStatus,
)


MINIMAL_ABOUT = """\
# Changelog

## 0.2.0 (in progress)

**1.1 Auth**
- Login form
- Session cookies

**Bug fixes**
- Fixed crash on startup

## 0.1.0

**1.1 Auth**
- Initial setup

# Roadmap

## Initiatives

### Reporting Push (Minor)
- 1.3.1

## Backlog
- Dark mode
- Mobile app
"""

INITIATIVES_ABOUT = """\
# Changelog

## 0.1.0

**1.1 Auth**
- Initial setup

# Roadmap

## Initiatives

### Dashboard Revamp (Minor)
- 1.2.1

### Reporting Push (Major)
- 1.3.1

## Backlog
- Future items
"""

NO_INITIATIVES_ABOUT = """\
# Changelog

## 0.1.0

**1.1 Auth**
- Initial setup

# Roadmap

## Backlog
- Future items
"""


def _product(statuses: dict) -> ProductDoc:
    features = [Feature(wbs=w, name=w, status=s) for w, s in statuses.items()]
    sa = WBSSubArea(wbs_prefix="1.1", title="Test", features=features)
    area = WBSArea(number=1, title="Core", sub_areas=[sa])
    return ProductDoc(raw_text="", wbs_areas=[area])


class TestParseText:
    def test_changelog_count(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        assert len(doc.changelog) == 2

    def test_in_progress_flag(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        assert doc.changelog[0].in_progress is True
        assert doc.changelog[0].version == "0.2.0"

    def test_completed_entry_not_in_progress(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        assert doc.changelog[1].in_progress is False
        assert doc.changelog[1].version == "0.1.0"

    def test_changelog_groups_parsed(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        entry = doc.changelog[0]
        assert len(entry.groups) == 1
        group = entry.groups[0]
        assert group.label == "1.1 Auth"
        assert "Login form" in group.items

    def test_bug_fixes_separated(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        entry = doc.changelog[0]
        assert "Fixed crash on startup" in entry.bug_fixes
        assert not any(g.label.lower() == "bug fixes" for g in entry.groups)

    def test_roadmap_sections(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        names = [s.name for s in doc.roadmap]
        assert "Backlog" in names

    def test_roadmap_backlog_items(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        sec = doc.roadmap_section("Backlog")
        assert "Dark mode" in sec.items

    def test_initiatives_parsed(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        assert len(doc.initiatives) == 1
        assert doc.initiatives[0].name == "Reporting Push"
        assert doc.initiatives[0].kind == "minor"
        assert doc.initiatives[0].items == ["1.3.1"]


class TestParseInitiatives:
    def test_single_initiative(self):
        body = "### Reporting Push (Minor)\n- 1.3.1\n"
        initiatives = parser._parse_initiatives(body)
        assert len(initiatives) == 1
        assert initiatives[0].name == "Reporting Push"
        assert initiatives[0].kind == "minor"
        assert initiatives[0].items == ["1.3.1"]

    def test_multiple_initiatives(self):
        doc = parser._parse_text(INITIATIVES_ABOUT)
        assert len(doc.initiatives) == 2
        assert doc.initiatives[0].name == "Dashboard Revamp"
        assert doc.initiatives[1].name == "Reporting Push"

    def test_major_kind_parsed(self):
        doc = parser._parse_text(INITIATIVES_ABOUT)
        major = next(i for i in doc.initiatives if i.name == "Reporting Push")
        assert major.kind == "major"
        assert major.items == ["1.3.1"]

    def test_defaults_to_minor_without_tag(self):
        body = "### Untagged Initiative\n- 1.9.9\n"
        initiatives = parser._parse_initiatives(body)
        assert initiatives[0].kind == "minor"


class TestTransformUpdateRoadmap:
    def test_updates_backlog(self):
        update = RoadmapUpdate(backlog=["Item A", "Item B"])
        result = parser.transform_update_roadmap(MINIMAL_ABOUT, update)
        doc = parser._parse_text(result)
        sec = doc.roadmap_section("Backlog")
        assert "Item A" in sec.items
        assert "Item B" in sec.items

    def test_clears_section(self):
        update = RoadmapUpdate(backlog=[])
        result = parser.transform_update_roadmap(MINIMAL_ABOUT, update)
        doc = parser._parse_text(result)
        sec = doc.roadmap_section("Backlog")
        assert sec.items == []


class TestTransformUpdateInitiatives:
    def test_replaces_existing_initiatives(self):
        update = [InitiativeUpdate(name="New Push", kind="major", wbs=["1.9.1"])]
        result = parser.transform_update_initiatives(INITIATIVES_ABOUT, update)
        doc = parser._parse_text(result)
        assert len(doc.initiatives) == 1
        assert doc.initiatives[0].name == "New Push"
        assert doc.initiatives[0].kind == "major"
        assert "Dashboard Revamp" not in [i.name for i in doc.initiatives]

    def test_writes_major_minor_tags(self):
        update = [
            InitiativeUpdate(name="A", kind="major", wbs=["1.1.1"]),
            InitiativeUpdate(name="B", kind="minor", wbs=["1.1.2"]),
        ]
        result = parser.transform_update_initiatives(MINIMAL_ABOUT, update)
        assert "### A (Major)" in result
        assert "### B (Minor)" in result

    def test_inserts_section_when_missing(self):
        update = [InitiativeUpdate(name="First One", kind="minor", wbs=["1.1.1"])]
        result = parser.transform_update_initiatives(NO_INITIATIVES_ABOUT, update)
        doc = parser._parse_text(result)
        assert len(doc.initiatives) == 1
        assert doc.initiatives[0].name == "First One"
        # Backlog content must survive the insertion
        bl = doc.roadmap_section("Backlog")
        assert bl and "Future items" in bl.items

    def test_preserves_backlog(self):
        update = [InitiativeUpdate(name="X", kind="minor", wbs=[])]
        result = parser.transform_update_initiatives(INITIATIVES_ABOUT, update)
        doc = parser._parse_text(result)
        bl = doc.roadmap_section("Backlog")
        assert bl and "Future items" in bl.items


class TestTransformAddChangelogEntry:
    def test_adds_entry_at_top(self):
        release = NewRelease(version="0.3.0")
        result = parser.transform_add_changelog_entry(MINIMAL_ABOUT, release, ["1.2 Dashboard"])
        doc = parser._parse_text(result)
        assert doc.changelog[0].version == "0.3.0"

    def test_includes_group_labels(self):
        release = NewRelease(version="0.3.0")
        result = parser.transform_add_changelog_entry(MINIMAL_ABOUT, release, ["1.2 Dashboard"])
        assert "**1.2 Dashboard**" in result

    def test_includes_bug_fixes(self):
        release = NewRelease(version="0.3.0", bug_fixes=["Fixed login bug"])
        result = parser.transform_add_changelog_entry(MINIMAL_ABOUT, release, [])
        assert "Fixed login bug" in result

    def test_raises_without_changelog_heading(self):
        with pytest.raises(ValueError, match="Changelog"):
            parser.transform_add_changelog_entry("No changelog here", NewRelease(version="1.0.0"), [])


class TestTransformClearCompletedInitiatives:
    def test_removes_initiative_when_all_features_done(self):
        product = _product({"1.2.1": FeatureStatus.live, "1.3.1": FeatureStatus.gap})
        result = parser.transform_clear_completed_initiatives(INITIATIVES_ABOUT, product)
        doc = parser._parse_text(result)
        names = [i.name for i in doc.initiatives]
        assert "Dashboard Revamp" not in names
        assert "Reporting Push" in names

    def test_keeps_initiative_when_not_all_done(self):
        product = _product({"1.2.1": FeatureStatus.gap, "1.3.1": FeatureStatus.gap})
        result = parser.transform_clear_completed_initiatives(INITIATIVES_ABOUT, product)
        doc = parser._parse_text(result)
        names = [i.name for i in doc.initiatives]
        assert "Dashboard Revamp" in names
        assert "Reporting Push" in names

    def test_removes_all_when_all_done(self):
        product = _product({"1.2.1": FeatureStatus.released, "1.3.1": FeatureStatus.live})
        result = parser.transform_clear_completed_initiatives(INITIATIVES_ABOUT, product)
        doc = parser._parse_text(result)
        assert doc.initiatives == []

    def test_no_op_when_no_initiatives(self):
        product = _product({})
        result = parser.transform_clear_completed_initiatives(MINIMAL_ABOUT.replace(
            "## Initiatives\n\n### Reporting Push (Minor)\n- 1.3.1\n\n", ""
        ), product)
        assert "## Backlog" in result

    def test_preserves_backlog(self):
        product = _product({"1.2.1": FeatureStatus.live, "1.3.1": FeatureStatus.live})
        result = parser.transform_clear_completed_initiatives(INITIATIVES_ABOUT, product)
        doc = parser._parse_text(result)
        bl = doc.roadmap_section("Backlog")
        assert bl and "Future items" in bl.items
