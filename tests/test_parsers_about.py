import pytest
from app.parsers import about as parser
from app.models import RoadmapUpdate, NewRelease, VersionBucket


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

## In Progress
- 1.2 Dashboard

## Planned
- 1.3 Reports
- 1.4 Admin

## Backlog
- Dark mode
- Mobile app
"""

BUCKETED_ABOUT = """\
# Changelog

## 0.1.0

**1.1 Auth**
- Initial setup

# Roadmap

## In Progress
- 1.1 Auth

## Planned
### v0.2.0
- 1.2 Dashboard

### v0.3.0
- 1.3 Reports

## Backlog
- Future items
"""


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
        assert "In Progress" in names
        assert "Planned" in names
        assert "Backlog" in names

    def test_roadmap_in_progress_items(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        sec = doc.roadmap_section("In Progress")
        assert sec is not None
        assert "1.2 Dashboard" in sec.items

    def test_roadmap_planned_items(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        sec = doc.roadmap_section("Planned")
        assert "1.3 Reports" in sec.items

    def test_roadmap_backlog_items(self):
        doc = parser._parse_text(MINIMAL_ABOUT)
        sec = doc.roadmap_section("Backlog")
        assert "Dark mode" in sec.items


class TestParsePlannedBuckets:
    def test_flat_planned(self):
        body = "- 1.2 Dashboard\n- 1.3 Reports\n"
        unassigned, buckets = parser._parse_planned_buckets(body)
        assert unassigned == ["1.2 Dashboard", "1.3 Reports"]
        assert buckets == []

    def test_bucketed_planned(self):
        doc = parser._parse_text(BUCKETED_ABOUT)
        sec = doc.roadmap_section("Planned")
        assert len(sec.buckets) == 2
        assert sec.buckets[0].label == "v0.2.0"
        assert "1.2 Dashboard" in sec.buckets[0].items
        assert sec.buckets[1].label == "v0.3.0"

    def test_mixed_buckets_and_unassigned(self):
        body = "- Unassigned item\n\n### v0.2.0\n- 1.2 Dashboard\n"
        unassigned, buckets = parser._parse_planned_buckets(body)
        assert "Unassigned item" in unassigned
        assert len(buckets) == 1


class TestTransformUpdateRoadmap:
    def test_updates_in_progress(self):
        update = RoadmapUpdate(in_progress=["1.3 New"], planned=[], backlog=[])
        result = parser.transform_update_roadmap(MINIMAL_ABOUT, update)
        doc = parser._parse_text(result)
        sec = doc.roadmap_section("In Progress")
        assert "1.3 New" in sec.items
        assert "1.2 Dashboard" not in sec.items

    def test_updates_backlog(self):
        update = RoadmapUpdate(in_progress=[], planned=[], backlog=["Item A", "Item B"])
        result = parser.transform_update_roadmap(MINIMAL_ABOUT, update)
        doc = parser._parse_text(result)
        sec = doc.roadmap_section("Backlog")
        assert "Item A" in sec.items
        assert "Item B" in sec.items

    def test_clears_section(self):
        update = RoadmapUpdate(in_progress=[], planned=[], backlog=[])
        result = parser.transform_update_roadmap(MINIMAL_ABOUT, update)
        doc = parser._parse_text(result)
        sec = doc.roadmap_section("In Progress")
        assert sec.items == []

    def test_updates_planned_with_buckets(self):
        bucket = VersionBucket(label="v0.3.0", items=["1.3 Reports"])
        update = RoadmapUpdate(
            in_progress=[], planned=[], backlog=[],
            planned_buckets=[bucket],
        )
        result = parser.transform_update_roadmap(MINIMAL_ABOUT, update)
        assert "### v0.3.0" in result
        assert "1.3 Reports" in result


class TestTransformAddChangelogEntry:
    def test_adds_entry_at_top(self):
        release = NewRelease(version="0.3.0")
        result = parser.transform_add_changelog_entry(MINIMAL_ABOUT, release, ["1.2 Dashboard"])
        doc = parser._parse_text(result)
        assert doc.changelog[0].version == "0.3.0"

    def test_clears_in_progress_section(self):
        release = NewRelease(version="0.2.0")
        result = parser.transform_add_changelog_entry(MINIMAL_ABOUT, release, [])
        doc = parser._parse_text(result)
        sec = doc.roadmap_section("In Progress")
        assert sec is None or sec.items == []

    def test_includes_in_progress_labels(self):
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


class TestTransformClearVersionBuckets:
    def test_removes_bucket_lte_released_version(self):
        result = parser.transform_clear_version_buckets(BUCKETED_ABOUT, "v0.2.0")
        doc = parser._parse_text(result)
        sec = doc.roadmap_section("Planned")
        labels = [b.label for b in sec.buckets] if sec else []
        assert "v0.2.0" not in labels
        assert "v0.3.0" in labels

    def test_keeps_bucket_gt_released_version(self):
        result = parser.transform_clear_version_buckets(BUCKETED_ABOUT, "v0.1.0")
        doc = parser._parse_text(result)
        sec = doc.roadmap_section("Planned")
        labels = [b.label for b in sec.buckets] if sec else []
        assert "v0.2.0" in labels
        assert "v0.3.0" in labels

    def test_removes_all_lte_buckets(self):
        result = parser.transform_clear_version_buckets(BUCKETED_ABOUT, "v0.3.0")
        doc = parser._parse_text(result)
        sec = doc.roadmap_section("Planned")
        assert not sec or not sec.buckets

    def test_no_op_when_no_buckets(self):
        result = parser.transform_clear_version_buckets(MINIMAL_ABOUT, "v1.0.0")
        assert result == MINIMAL_ABOUT

    def test_preserves_non_semver_buckets(self):
        about_with_freeform = BUCKETED_ABOUT.replace("### v0.3.0", "### Q2 2026")
        result = parser.transform_clear_version_buckets(about_with_freeform, "v0.5.0")
        doc = parser._parse_text(result)
        sec = doc.roadmap_section("Planned")
        labels = [b.label for b in sec.buckets] if sec else []
        assert "Q2 2026" in labels

    def test_preserves_in_progress_and_backlog(self):
        result = parser.transform_clear_version_buckets(BUCKETED_ABOUT, "v0.2.0")
        doc = parser._parse_text(result)
        ip = doc.roadmap_section("In Progress")
        bl = doc.roadmap_section("Backlog")
        assert ip and "1.1 Auth" in ip.items
        assert bl and "Future items" in bl.items
