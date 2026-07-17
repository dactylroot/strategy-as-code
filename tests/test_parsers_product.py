import pytest
from app.parsers import product as parser
from app.models import FeatureStatus, NewFeature


MINIMAL_PRODUCT = """\
# Test Product - Product Overview

## Summary
A test product.

## Users

### Admin
Test admin.

## Product Scope

### Core
- Feature A
- ~~Done~~ 🎆

## Core Workflows

### Admin Workflow
1. Step one

## Features

### 1. Core

#### 1.1 Auth

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.1.1 | Login | Live | 8 | 3 | Auth login |
| 1.1.2 | Logout | Scoped | | | |
| 1.1.3 | Reset | Gap | | | Not started |

#### 1.2 Dashboard

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.2.1 | Overview | Scored | 7 | 2 | Main |
| 1.2.2 | Stats | In-Progress | | | |

## Known Gaps for Team Discussion

### Auth Gap
Need password reset.
"""

FLAGGED_PRODUCT = """\
# Flagged Product

## Features

### 1. Core

#### 1.1 Auth

| WBS | Feature | Status | Value | Effort | Notes | Flag |
| --- | ------- | ------ | ----- | ------ | ----- | ---- |
| 1.1.1 | Login | Live | 8 | 3 | Auth login | gap |
| 1.1.2 | Logout | Scoped | | | | |
"""

LEGACY_PRODUCT = """\
# Legacy Product

## Features

### 1. Core

#### 1.1 Auth

| WBS | Feature | Status | Notes |
| --- | ------- | ------ | ----- |
| 1.1.1 | Login | Live | Works |
| 1.1.2 | Logout | Scoped | Pending |
"""


class TestParseText:
    def test_title_stripped(self):
        doc = parser._parse_text(MINIMAL_PRODUCT)
        assert doc.title == "Test Product"

    def test_features_count(self):
        doc = parser._parse_text(MINIMAL_PRODUCT)
        assert doc.total_features == 5

    def test_feature_values_parsed(self):
        doc = parser._parse_text(MINIMAL_PRODUCT)
        login = next(f for f in doc.all_features if f.wbs == "1.1.1")
        assert login.value == 8
        assert login.effort == 3
        assert login.status == FeatureStatus.live

    def test_blank_value_effort_is_none(self):
        doc = parser._parse_text(MINIMAL_PRODUCT)
        logout = next(f for f in doc.all_features if f.wbs == "1.1.2")
        assert logout.value is None
        assert logout.effort is None

    def test_legacy_4col_parsed(self):
        doc = parser._parse_text(LEGACY_PRODUCT)
        assert doc.total_features == 2
        login = next(f for f in doc.all_features if f.wbs == "1.1.1")
        assert login.status == FeatureStatus.live
        assert login.notes == "Works"
        assert login.value is None

    def test_users_parsed(self):
        doc = parser._parse_text(MINIMAL_PRODUCT)
        assert len(doc.users) == 1
        assert doc.users[0].name == "Admin"

    def test_scope_groups_parsed(self):
        doc = parser._parse_text(MINIMAL_PRODUCT)
        assert len(doc.scope_groups) == 1
        core = doc.scope_groups[0]
        items = {i.text: i.complete for i in core.items}
        assert items["Feature A"] is False
        assert items["Done"] is True

    def test_wbs_hierarchy(self):
        doc = parser._parse_text(MINIMAL_PRODUCT)
        assert len(doc.wbs_areas) == 1
        area = doc.wbs_areas[0]
        assert area.number == 1
        assert len(area.sub_areas) == 2
        assert area.sub_areas[0].wbs_prefix == "1.1"
        assert area.sub_areas[1].wbs_prefix == "1.2"

    def test_legacy_scoped_text_normalized_to_idea(self):
        """Legacy literal 'Scoped' status text (from files predating the
        derived-stage model) is normalized to Idea on parse."""
        doc = parser._parse_text(MINIMAL_PRODUCT)
        logout = next(f for f in doc.all_features if f.wbs == "1.1.2")
        assert logout.status == FeatureStatus.idea
        assert logout.stage == "Idea"  # blank notes - derivation doesn't promote it

    def test_legacy_scored_text_normalized_but_still_derives_scored(self):
        doc = parser._parse_text(MINIMAL_PRODUCT)
        overview = next(f for f in doc.all_features if f.wbs == "1.2.1")
        assert overview.status == FeatureStatus.idea
        assert overview.stage == "Scored"  # has notes + both scores


class TestGetFeatureStatus:
    def test_finds_status(self):
        status = parser.get_feature_status(MINIMAL_PRODUCT, "1.1.1")
        assert status == FeatureStatus.live

    def test_not_found_returns_none(self):
        assert parser.get_feature_status(MINIMAL_PRODUCT, "9.9.9") is None

    def test_in_progress_status(self):
        status = parser.get_feature_status(MINIMAL_PRODUCT, "1.2.2")
        assert status == FeatureStatus.in_progress

    def test_legacy_scoped_text_normalized(self):
        status = parser.get_feature_status(MINIMAL_PRODUCT, "1.1.2")
        assert status == FeatureStatus.idea


class TestTransformFeatureStatus:
    def test_updates_status(self):
        result = parser.transform_feature_status(MINIMAL_PRODUCT, "1.1.2", FeatureStatus.in_progress)
        assert "| 1.1.2 | Logout | In-Progress |" in result

    def test_raises_on_missing_wbs(self):
        with pytest.raises(ValueError, match="9.9.9"):
            parser.transform_feature_status(MINIMAL_PRODUCT, "9.9.9", FeatureStatus.live)

    def test_does_not_affect_other_rows(self):
        result = parser.transform_feature_status(MINIMAL_PRODUCT, "1.1.2", FeatureStatus.in_progress)
        assert "| 1.1.1 | Login | Live |" in result


class TestTransformFeatureName:
    def test_updates_name(self):
        result = parser.transform_feature_name(MINIMAL_PRODUCT, "1.1.2", "Sign Out")
        assert "| 1.1.2 | Sign Out |" in result
        assert "Logout" not in result.split("1.1.2")[1].split("\n")[0]

    def test_raises_on_missing_wbs(self):
        with pytest.raises(ValueError):
            parser.transform_feature_name(MINIMAL_PRODUCT, "9.9.9", "Name")


class TestTransformFeatureNotes:
    def test_updates_notes(self):
        result = parser.transform_feature_notes(MINIMAL_PRODUCT, "1.1.1", "Updated note")
        doc = parser._parse_text(result)
        login = next(f for f in doc.all_features if f.wbs == "1.1.1")
        assert login.notes == "Updated note"

    def test_updates_empty_notes(self):
        result = parser.transform_feature_notes(MINIMAL_PRODUCT, "1.1.2", "New note")
        doc = parser._parse_text(result)
        logout = next(f for f in doc.all_features if f.wbs == "1.1.2")
        assert logout.notes == "New note"

    def test_normalises_newlines(self):
        # Raw newlines would break the markdown table row, so they're encoded
        # as <br> (templates/JS decode this back to \n for editing/rendering).
        result = parser.transform_feature_notes(MINIMAL_PRODUCT, "1.1.1", "line1\nline2")
        assert "line1<br>line2" in result


CORRUPTED_PRODUCT = """\
# Corrupted Product

## Features

### 1. Core

#### 1.1 Auth

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.1.1 | Login | Live | 8 | 3 | First part of the note explaining the issue.

AM Tested: still broken, more detail here. |
| 1.1.2 | Logout | Scoped | | | Fine |
"""


class TestCorruptedRowRecovery:
    def test_parse_recovers_row_split_by_a_raw_line_break(self):
        # Simulates a file saved before notes were escaped: 1.1.1's note has
        # a literal blank-line break, splitting its row across 3 lines.
        doc = parser._parse_text(CORRUPTED_PRODUCT)
        wbs_codes = [f.wbs for f in doc.all_features]
        assert "1.1.1" in wbs_codes
        assert "1.1.2" in wbs_codes  # the row after the corrupted one must not be swallowed
        login = next(f for f in doc.all_features if f.wbs == "1.1.1")
        assert "First part of the note" in login.notes
        assert "AM Tested" in login.notes

    def test_update_self_heals_a_previously_corrupted_row(self):
        # Before the fix this raised ValueError("Expected 1 match for WBS
        # '1.1.1', got 0") because the corrupted row spans 3 raw lines and
        # the update regex only matches a single line.
        result = parser.transform_feature_status(CORRUPTED_PRODUCT, "1.1.1", FeatureStatus.released)
        doc = parser._parse_text(result)
        login = next(f for f in doc.all_features if f.wbs == "1.1.1")
        assert login.status == FeatureStatus.released
        assert "First part of the note" in login.notes
        assert "\n\nAM Tested" not in result  # rejoined onto a single physical line


class TestTransformFeatureScore:
    def test_sets_both_values(self):
        result = parser.transform_feature_score(MINIMAL_PRODUCT, "1.1.2", 6, 3)
        doc = parser._parse_text(result)
        f = next(x for x in doc.all_features if x.wbs == "1.1.2")
        assert f.value == 6
        assert f.effort == 3

    def test_clears_both_values(self):
        result = parser.transform_feature_score(MINIMAL_PRODUCT, "1.1.1", None, None)
        doc = parser._parse_text(result)
        f = next(x for x in doc.all_features if x.wbs == "1.1.1")
        assert f.value is None
        assert f.effort is None

    def test_clears_one_value(self):
        result = parser.transform_feature_score(MINIMAL_PRODUCT, "1.1.1", 8, None)
        doc = parser._parse_text(result)
        f = next(x for x in doc.all_features if x.wbs == "1.1.1")
        assert f.value == 8
        assert f.effort is None

    def test_upgrades_legacy_4col(self):
        result = parser.transform_feature_score(LEGACY_PRODUCT, "1.1.2", 5, 2)
        doc = parser._parse_text(result)
        f = next(x for x in doc.all_features if x.wbs == "1.1.2")
        assert f.value == 5
        assert f.effort == 2

    def test_raises_on_missing_wbs(self):
        with pytest.raises(ValueError):
            parser.transform_feature_score(MINIMAL_PRODUCT, "9.9.9", 5, 2)


class TestTransformAddFeature:
    def test_adds_feature(self):
        req = NewFeature(wbs_prefix="1.1", name="MFA", status=FeatureStatus.idea)
        result, feat = parser.transform_add_feature(MINIMAL_PRODUCT, req)
        assert feat.wbs == "1.1.4"
        assert feat.name == "MFA"
        doc = parser._parse_text(result)
        wbses = [f.wbs for f in doc.all_features]
        assert "1.1.4" in wbses

    def test_increments_wbs_correctly(self):
        req = NewFeature(wbs_prefix="1.2", name="Charts")
        result, feat = parser.transform_add_feature(MINIMAL_PRODUCT, req)
        assert feat.wbs == "1.2.3"

    def test_with_score(self):
        req = NewFeature(wbs_prefix="1.1", name="New feat", status=FeatureStatus.idea, value=7, effort=3)
        result, feat = parser.transform_add_feature(MINIMAL_PRODUCT, req)
        doc = parser._parse_text(result)
        new_f = next(f for f in doc.all_features if f.name == "New feat")
        assert new_f.value == 7
        assert new_f.effort == 3

    def test_raises_on_unknown_prefix(self):
        req = NewFeature(wbs_prefix="9.9", name="X")
        with pytest.raises(ValueError, match="9.9"):
            parser.transform_add_feature(MINIMAL_PRODUCT, req)


class TestTransformFeatureFlagged:
    def test_sets_flag_on_6col_row(self):
        result = parser.transform_feature_flagged(MINIMAL_PRODUCT, "1.1.2", True)
        doc = parser._parse_text(result)
        f = next(x for x in doc.all_features if x.wbs == "1.1.2")
        assert f.flagged is True

    def test_clears_flag(self):
        result = parser.transform_feature_flagged(FLAGGED_PRODUCT, "1.1.1", False)
        doc = parser._parse_text(result)
        f = next(x for x in doc.all_features if x.wbs == "1.1.1")
        assert f.flagged is False

    def test_flag_does_not_corrupt_notes(self):
        result = parser.transform_feature_flagged(MINIMAL_PRODUCT, "1.1.1", True)
        doc = parser._parse_text(result)
        f = next(x for x in doc.all_features if x.wbs == "1.1.1")
        assert f.notes == "Auth login"
        assert f.flagged is True

    def test_clear_flag_preserves_notes(self):
        result = parser.transform_feature_flagged(FLAGGED_PRODUCT, "1.1.1", False)
        doc = parser._parse_text(result)
        f = next(x for x in doc.all_features if x.wbs == "1.1.1")
        assert f.notes == "Auth login"

    def test_idempotent_set(self):
        r1 = parser.transform_feature_flagged(MINIMAL_PRODUCT, "1.1.1", True)
        r2 = parser.transform_feature_flagged(r1, "1.1.1", True)
        doc = parser._parse_text(r2)
        f = next(x for x in doc.all_features if x.wbs == "1.1.1")
        assert f.flagged is True

    def test_upgrades_legacy_4col_when_flagging(self):
        result = parser.transform_feature_flagged(LEGACY_PRODUCT, "1.1.1", True)
        doc = parser._parse_text(result)
        f = next(x for x in doc.all_features if x.wbs == "1.1.1")
        assert f.flagged is True
        assert f.notes == "Works"

    def test_raises_on_missing_wbs(self):
        with pytest.raises(ValueError):
            parser.transform_feature_flagged(MINIMAL_PRODUCT, "9.9.9", True)


class TestFlaggedParsing:
    def test_flagged_true_parsed(self):
        doc = parser._parse_text(FLAGGED_PRODUCT)
        login = next(f for f in doc.all_features if f.wbs == "1.1.1")
        assert login.flagged is True

    def test_unflagged_parsed_as_false(self):
        doc = parser._parse_text(FLAGGED_PRODUCT)
        logout = next(f for f in doc.all_features if f.wbs == "1.1.2")
        assert logout.flagged is False

    def test_6col_defaults_to_unflagged(self):
        doc = parser._parse_text(MINIMAL_PRODUCT)
        for f in doc.all_features:
            assert f.flagged is False


class TestTransformMoveFeature:
    def test_moves_feature(self):
        result, feat = parser.transform_move_feature(MINIMAL_PRODUCT, "1.1.2", "1.2")
        doc = parser._parse_text(result)
        wbses = [f.wbs for f in doc.all_features]
        assert "1.1.2" not in wbses
        assert feat.wbs.startswith("1.2.")
        assert feat.name == "Logout"

    def test_raises_on_missing_wbs(self):
        with pytest.raises(ValueError):
            parser.transform_move_feature(MINIMAL_PRODUCT, "9.9.9", "1.2")
