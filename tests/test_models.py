import pytest
from app.models import (
    Feature, FeatureStatus, WBSSubArea, WBSArea, ProductDoc,
    AboutDoc, ChangelogEntry, ChangelogGroup, RoadmapSection,
)


class TestFeaturePriorityScore:
    def test_both_values(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea, value=8, effort=4)
        assert f.priority_score == 2.0

    def test_value_none(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea, value=None, effort=3)
        assert f.priority_score is None

    def test_effort_none(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea, value=8, effort=None)
        assert f.priority_score is None

    def test_both_none(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.gap)
        assert f.priority_score is None

    def test_zero_effort_returns_none(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea, value=5, effort=0)
        assert f.priority_score is None

    def test_fractional_score(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea, value=7, effort=2)
        assert f.priority_score == 3.5


class TestFeatureStage:
    """Scoped/Scored are derived from Notes/Value/Effort, never stored - see
    the Feature Lifecycle section of the program-strategy SKILL.md."""

    def test_idea_without_notes_stays_idea(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea)
        assert f.stage == "Idea"

    def test_idea_with_notes_becomes_scoped(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea, notes="A real description")
        assert f.stage == "Scoped"

    def test_idea_with_notes_and_scores_becomes_scored(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea, notes="desc", value=8, effort=4)
        assert f.stage == "Scored"

    def test_idea_with_only_one_score_stays_scoped(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea, notes="desc", value=8, effort=None)
        assert f.stage == "Scoped"

    def test_gap_with_notes_and_scores_becomes_scored(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.gap, notes="desc", value=8, effort=4)
        assert f.stage == "Scored"

    def test_gap_without_notes_stays_gap(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.gap)
        assert f.stage == "Gap"

    def test_whitespace_only_notes_do_not_count(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea, notes="   ")
        assert f.stage == "Idea"

    def test_in_progress_passthrough(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.in_progress, notes="desc", value=8, effort=4)
        assert f.stage == "In-Progress"

    def test_live_passthrough(self):
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.live)
        assert f.stage == "Live"


class TestWBSSubAreaMetrics:
    def _make_sa(self, statuses):
        features = [
            Feature(wbs=f"1.1.{i+1}", name=f"F{i}", status=s)
            for i, s in enumerate(statuses)
        ]
        return WBSSubArea(wbs_prefix="1.1", title="Auth", features=features)

    def test_empty_sub_area(self):
        sa = WBSSubArea(wbs_prefix="1.1", title="Auth")
        assert sa.completion_pct == 0.0
        assert sa.live_count == 0
        assert sa.planned_count == 0
        assert sa.gap_count == 0

    def test_all_live(self):
        sa = self._make_sa([FeatureStatus.live, FeatureStatus.live])
        assert sa.completion_pct == 1.0
        assert sa.live_count == 2

    def test_mixed_statuses(self):
        sa = self._make_sa([
            FeatureStatus.live,
            FeatureStatus.in_progress,
            FeatureStatus.idea,
            FeatureStatus.gap,
        ])
        assert sa.live_count == 1
        assert sa.planned_count == 1
        assert sa.gap_count == 2
        assert sa.completion_pct == 0.25

    def test_planned_alias_counted(self):
        sa = self._make_sa([FeatureStatus.planned])
        assert sa.planned_count == 1

    def test_scored_idea_still_counted_as_gap(self):
        """gap_count groups by raw status - a Gap/Idea that reads as Scored
        via .stage is still an Idea underneath, so it still counts here."""
        f = Feature(wbs="1.1.1", name="X", status=FeatureStatus.idea, notes="desc", value=8, effort=4)
        assert f.stage == "Scored"
        sa = WBSSubArea(wbs_prefix="1.1", title="Auth", features=[f])
        assert sa.gap_count == 1
        assert sa.planned_count == 0


class TestWBSAreaAggregates:
    def test_all_features_flattened(self):
        sa1 = WBSSubArea(wbs_prefix="1.1", title="A", features=[
            Feature(wbs="1.1.1", name="F1", status=FeatureStatus.live),
        ])
        sa2 = WBSSubArea(wbs_prefix="1.2", title="B", features=[
            Feature(wbs="1.2.1", name="F2", status=FeatureStatus.gap),
            Feature(wbs="1.2.2", name="F3", status=FeatureStatus.live),
        ])
        area = WBSArea(number=1, title="Core", sub_areas=[sa1, sa2])
        assert len(area.all_features) == 3
        assert area.live_count == 2
        assert area.gap_count == 1
        assert area.completion_pct == pytest.approx(2 / 3)


class TestProductDocAggregates:
    def _make_doc(self):
        features = [
            Feature(wbs="1.1.1", name="Login", status=FeatureStatus.live, value=8, effort=3),
            Feature(wbs="1.1.2", name="Logout", status=FeatureStatus.idea),
            Feature(wbs="1.1.3", name="Reset", status=FeatureStatus.gap),
        ]
        sa = WBSSubArea(wbs_prefix="1.1", title="Auth", features=features)
        area = WBSArea(number=1, title="Core", sub_areas=[sa])
        return ProductDoc(raw_text="", wbs_areas=[area])

    def test_total_features(self):
        assert self._make_doc().total_features == 3

    def test_live_count(self):
        assert self._make_doc().live_count == 1

    def test_gap_count(self):
        assert self._make_doc().gap_count == 2

    def test_completion_pct(self):
        assert self._make_doc().overall_completion_pct == pytest.approx(1 / 3)

    def test_empty_doc(self):
        doc = ProductDoc(raw_text="")
        assert doc.total_features == 0
        assert doc.overall_completion_pct == 0.0


class TestAboutDocRoadmapSection:
    def test_finds_section(self):
        doc = AboutDoc(raw_text="", roadmap=[
            RoadmapSection(name="In Progress", items=["1.1 Auth"]),
            RoadmapSection(name="Planned", items=["1.2 Dashboard"]),
        ])
        sec = doc.roadmap_section("In Progress")
        assert sec is not None
        assert sec.items == ["1.1 Auth"]

    def test_missing_section_returns_none(self):
        doc = AboutDoc(raw_text="", roadmap=[])
        assert doc.roadmap_section("Backlog") is None

    def test_changelog_in_progress_flag(self):
        entry = ChangelogEntry(version="0.2.0", in_progress=True, groups=[
            ChangelogGroup(label="1.1 Auth", items=["Login added"]),
        ])
        doc = AboutDoc(raw_text="", changelog=[entry])
        assert doc.changelog[0].in_progress is True
        assert doc.changelog[0].groups[0].label == "1.1 Auth"
