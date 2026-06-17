import pytest
from app.models import (
    AboutDoc, ChangelogEntry, ChangelogGroup, RoadmapSection,
    ProductDoc, Feature, FeatureStatus, WBSArea, WBSSubArea,
)
from app.versioning import next_release_version, version_rationale


def _about(changelog=None, roadmap=None):
    return AboutDoc(
        raw_text="",
        changelog=changelog or [],
        roadmap=roadmap or [],
    )


def _ip_entry(version, labels=None):
    groups = [ChangelogGroup(label=l, items=[]) for l in (labels or [])]
    return ChangelogEntry(version=version, in_progress=True, groups=groups)


def _done_entry(version):
    return ChangelogEntry(version=version, in_progress=False)


class TestNextReleaseVersion:
    def test_in_progress_entry_returned_directly(self):
        about = _about(changelog=[_ip_entry("0.3.0"), _done_entry("0.2.0")])
        assert next_release_version(about) == "0.3.0"

    def test_minor_bump_when_sub_areas_in_progress(self):
        about = _about(
            changelog=[_done_entry("0.2.0")],
            roadmap=[RoadmapSection(name="In Progress", items=["1.1 Auth"])],
        )
        assert next_release_version(about) == "0.3.0"

    def test_release_bump_when_no_sub_areas(self):
        about = _about(
            changelog=[_done_entry("0.2.0")],
            roadmap=[RoadmapSection(name="In Progress", items=[])],
        )
        assert next_release_version(about) == "0.2.1"

    def test_release_bump_with_no_roadmap_section(self):
        about = _about(changelog=[_done_entry("0.1.0")])
        assert next_release_version(about) == "0.1.1"

    def test_empty_changelog_starts_at_zero(self):
        about = _about(roadmap=[RoadmapSection(name="In Progress", items=["1.1 Auth"])])
        assert next_release_version(about) == "0.1.0"

    def test_picks_highest_shipped_version(self):
        about = _about(
            changelog=[_done_entry("0.3.0"), _done_entry("0.1.0"), _done_entry("0.2.0")],
            roadmap=[RoadmapSection(name="In Progress", items=["1.1 Auth"])],
        )
        assert next_release_version(about) == "0.4.0"

    def test_full_completion_returns_1_0_0(self):
        about = _about(changelog=[_done_entry("0.5.0")])
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.live)
        sa = WBSSubArea(wbs_prefix="1.1", title="A", features=[f])
        area = WBSArea(number=1, title="Core", sub_areas=[sa])
        product = ProductDoc(raw_text="", wbs_areas=[area])
        assert next_release_version(about, product) == "1.0.0"

    def test_incomplete_product_not_1_0_0(self):
        about = _about(changelog=[_done_entry("0.5.0")])
        features = [
            Feature(wbs="1.1.1", name="X", status=FeatureStatus.live),
            Feature(wbs="1.1.2", name="Y", status=FeatureStatus.gap),
        ]
        sa = WBSSubArea(wbs_prefix="1.1", title="A", features=features)
        area = WBSArea(number=1, title="Core", sub_areas=[sa])
        product = ProductDoc(raw_text="", wbs_areas=[area])
        result = next_release_version(about, product)
        assert result != "1.0.0"


class TestVersionRationale:
    def test_single_in_progress_group(self):
        about = _about(changelog=[_ip_entry("0.3.0", ["1.1 Auth"])])
        assert version_rationale(about) == "Shipping: 1.1 Auth"

    def test_multiple_groups(self):
        about = _about(changelog=[_ip_entry("0.3.0", ["1.1 Auth", "1.2 Dashboard"])])
        assert version_rationale(about) == "Shipping: 1.1 Auth, 1.2 Dashboard"

    def test_bug_fixes_only_skips_label(self):
        entry = ChangelogEntry(
            version="0.3.0", in_progress=True,
            groups=[ChangelogGroup(label="Bug fixes", items=["Fix crash"])],
        )
        about = _about(changelog=[entry])
        assert version_rationale(about) == "Bug fixes only"

    def test_no_in_progress_single_roadmap_item(self):
        about = _about(roadmap=[RoadmapSection(name="In Progress", items=["1.1 Auth"])])
        assert version_rationale(about) == "1 sub-area shipping: 1.1 Auth"

    def test_no_in_progress_multiple_roadmap_items(self):
        about = _about(roadmap=[RoadmapSection(name="In Progress", items=["1.1 Auth", "1.2 Dashboard"])])
        assert version_rationale(about) == "2 sub-areas shipping: 1.1 Auth, 1.2 Dashboard"

    def test_no_in_progress_empty_roadmap(self):
        about = _about()
        result = version_rationale(about)
        assert "bug-fix" in result.lower()
