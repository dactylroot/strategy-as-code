import pytest
from app.models import (
    AboutDoc, ChangelogEntry, ChangelogGroup, Initiative,
    ProductDoc, Feature, FeatureStatus, WBSArea, WBSSubArea,
)
from app.versioning import next_release_version, version_rationale


def _about(changelog=None, initiatives=None):
    return AboutDoc(
        raw_text="",
        changelog=changelog or [],
        initiatives=initiatives or [],
    )


def _ip_entry(version, labels=None):
    groups = [ChangelogGroup(label=l, items=[]) for l in (labels or [])]
    return ChangelogEntry(version=version, in_progress=True, groups=groups)


def _done_entry(version):
    return ChangelogEntry(version=version, in_progress=False)


def _product(statuses: dict) -> ProductDoc:
    features = [Feature(wbs=w, name=w, status=s) for w, s in statuses.items()]
    sa = WBSSubArea(wbs_prefix="1.1", title="A", features=features)
    area = WBSArea(number=1, title="Core", sub_areas=[sa])
    return ProductDoc(raw_text="", wbs_areas=[area])


class TestNextReleaseVersion:
    def test_in_progress_entry_returned_directly(self):
        about = _about(changelog=[_ip_entry("0.3.0"), _done_entry("0.2.0")])
        assert next_release_version(about) == "0.3.0"

    def test_minor_bump_when_minor_initiative_complete(self):
        # An extra, unrelated incomplete feature keeps overall completion
        # below 100% so the 1.0.0 override doesn't mask the minor bump.
        product = _product({"1.1.1": FeatureStatus.live, "1.1.9": FeatureStatus.gap})
        about = _about(
            changelog=[_done_entry("0.2.0")],
            initiatives=[Initiative(name="Reporting", kind="minor", items=["1.1.1"])],
        )
        assert next_release_version(about, product) == "0.3.0"

    def test_major_bump_when_major_initiative_complete(self):
        product = _product({"1.1.1": FeatureStatus.released})
        about = _about(
            changelog=[_done_entry("0.2.0")],
            initiatives=[Initiative(name="Overhaul", kind="major", items=["1.1.1"])],
        )
        assert next_release_version(about, product) == "1.0.0"

    def test_major_wins_over_minor_when_both_complete(self):
        product = _product({"1.1.1": FeatureStatus.live, "1.1.2": FeatureStatus.live})
        about = _about(
            changelog=[_done_entry("0.2.0")],
            initiatives=[
                Initiative(name="Minor One", kind="minor", items=["1.1.1"]),
                Initiative(name="Major One", kind="major", items=["1.1.2"]),
            ],
        )
        assert next_release_version(about, product) == "1.0.0"

    def test_release_bump_when_no_initiative_complete(self):
        product = _product({"1.1.1": FeatureStatus.gap})
        about = _about(
            changelog=[_done_entry("0.2.0")],
            initiatives=[Initiative(name="Reporting", kind="minor", items=["1.1.1"])],
        )
        assert next_release_version(about, product) == "0.2.1"

    def test_major_bump_distinct_from_all_scope_override(self):
        """A major initiative bump increments MAJOR by one - it isn't the
        same mechanism as the 1.0.0 all-scope-complete override."""
        product = _product({"1.1.1": FeatureStatus.live, "1.1.9": FeatureStatus.gap})
        about = _about(
            changelog=[_done_entry("2.5.0")],
            initiatives=[Initiative(name="Overhaul", kind="major", items=["1.1.1"])],
        )
        assert next_release_version(about, product) == "3.0.0"

    def test_release_bump_with_no_initiatives(self):
        about = _about(changelog=[_done_entry("0.1.0")])
        assert next_release_version(about) == "0.1.1"

    def test_empty_changelog_starts_at_zero(self):
        about = _about()
        assert next_release_version(about) == "0.0.1"

    def test_picks_highest_shipped_version(self):
        product = _product({"1.1.1": FeatureStatus.live, "1.1.9": FeatureStatus.gap})
        about = _about(
            changelog=[_done_entry("0.3.0"), _done_entry("0.1.0"), _done_entry("0.2.0")],
            initiatives=[Initiative(name="Reporting", kind="minor", items=["1.1.1"])],
        )
        assert next_release_version(about, product) == "0.4.0"

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

    def test_minor_initiative_complete(self):
        product = _product({"1.1.1": FeatureStatus.live})
        about = _about(initiatives=[Initiative(name="Reporting", kind="minor", items=["1.1.1"])])
        assert version_rationale(about, product) == "Minor initiative complete: Reporting"

    def test_major_initiative_complete(self):
        product = _product({"1.1.1": FeatureStatus.live})
        about = _about(initiatives=[Initiative(name="Overhaul", kind="major", items=["1.1.1"])])
        assert version_rationale(about, product) == "Major initiative complete: Overhaul"

    def test_no_initiatives_complete(self):
        result = version_rationale(_about())
        assert "bug-fix" in result.lower()
