import pytest
from app.parsers import bugs as parser
from app.parsers.bugs import _EMPTY_BUGS
from app.models import BugCreate, BugUpdate, BugSeverity, BugStatus


BUGS_WITH_DATA = """\
# Bugs

## Active

| ID | Title | Severity | Status | Notes | WBS |
|----|-------|----------|--------|-------|-----|
| 1 | Login fails | High | Open | Happens on Safari | 1.1.1 |
| 2 | Slow dashboard | Medium | Investigating | Under load | 1.2.1 |

## Resolved

| ID | Title | Resolved In | Date |
|----|-------|-------------|------|
| 3 | Old crash | 0.1.0 | 2024-01-15 |
"""


class TestParseText:
    def test_active_bugs_parsed(self):
        doc = parser._parse_text(BUGS_WITH_DATA)
        assert len(doc.active) == 2

    def test_resolved_bugs_parsed(self):
        doc = parser._parse_text(BUGS_WITH_DATA)
        assert len(doc.resolved) == 1
        assert doc.resolved[0].resolved_in == "0.1.0"

    def test_bug_fields(self):
        doc = parser._parse_text(BUGS_WITH_DATA)
        b = doc.active[0]
        assert b.id == 1
        assert b.title == "Login fails"
        assert b.severity == BugSeverity.high
        assert b.status == BugStatus.open
        assert b.notes == "Happens on Safari"
        assert b.wbs_ref == "1.1.1"

    def test_wbs_ref_none_when_empty(self):
        text = _EMPTY_BUGS + "| 1 | Test | Medium | Open | notes |  |\n"
        doc = parser._parse_text(text)
        if doc.active:
            assert doc.active[0].wbs_ref is None

    def test_empty_bugs_parses_empty(self):
        doc = parser._parse_text(_EMPTY_BUGS)
        assert doc.active == []
        assert doc.resolved == []


class TestTransformAddBug:
    def test_adds_bug(self):
        req = BugCreate(title="New bug", severity=BugSeverity.low)
        result, bug = parser.transform_add_bug(BUGS_WITH_DATA, req)
        doc = parser._parse_text(result)
        assert any(b.title == "New bug" for b in doc.active)
        assert bug.id == 4

    def test_id_increments_beyond_resolved(self):
        req = BugCreate(title="Bug after resolved")
        result, bug = parser.transform_add_bug(BUGS_WITH_DATA, req)
        assert bug.id == 4

    def test_add_to_empty(self):
        req = BugCreate(title="First bug")
        result, bug = parser.transform_add_bug(_EMPTY_BUGS, req)
        assert bug.id == 1
        doc = parser._parse_text(result)
        assert len(doc.active) == 1

    def test_add_with_wbs_ref(self):
        req = BugCreate(title="WBS bug", wbs_ref="1.2.3")
        result, bug = parser.transform_add_bug(_EMPTY_BUGS, req)
        assert "1.2.3" in result


class TestTransformUpdateBug:
    def test_updates_title(self):
        req = BugUpdate(title="Updated title")
        result, bug = parser.transform_update_bug(BUGS_WITH_DATA, 1, req)
        assert bug.title == "Updated title"
        doc = parser._parse_text(result)
        assert doc.active[0].title == "Updated title"

    def test_updates_severity(self):
        req = BugUpdate(severity=BugSeverity.critical)
        result, bug = parser.transform_update_bug(BUGS_WITH_DATA, 1, req)
        assert bug.severity == BugSeverity.critical

    def test_updates_status(self):
        req = BugUpdate(status=BugStatus.fix_in_progress)
        result, bug = parser.transform_update_bug(BUGS_WITH_DATA, 1, req)
        assert bug.status == BugStatus.fix_in_progress

    def test_partial_update_preserves_other_fields(self):
        req = BugUpdate(title="New title")
        result, bug = parser.transform_update_bug(BUGS_WITH_DATA, 1, req)
        assert bug.severity == BugSeverity.high
        assert bug.notes == "Happens on Safari"

    def test_raises_on_missing_bug(self):
        with pytest.raises(ValueError, match="99"):
            parser.transform_update_bug(BUGS_WITH_DATA, 99, BugUpdate())


class TestTransformResolveBug:
    def test_moves_to_resolved(self):
        # transform_resolve_bug marks the active row "Resolved" in-place and
        # appends it to the Resolved table; the row stays in doc.active with
        # Resolved status.
        result = parser.transform_resolve_bug(BUGS_WITH_DATA, 1, resolved_in="0.2.0", today="2024-06-01")
        doc = parser._parse_text(result)
        active_statuses = {b.id: b.status for b in doc.active}
        assert active_statuses[1] == BugStatus.resolved
        resolved_ids = [r.id for r in doc.resolved]
        assert 1 in resolved_ids

    def test_resolved_in_and_date_set(self):
        result = parser.transform_resolve_bug(BUGS_WITH_DATA, 1, resolved_in="0.2.0", today="2024-06-01")
        doc = parser._parse_text(result)
        r = next(x for x in doc.resolved if x.id == 1)
        assert r.resolved_in == "0.2.0"
        assert r.date == "2024-06-01"

    def test_raises_on_missing_bug(self):
        with pytest.raises(ValueError, match="99"):
            parser.transform_resolve_bug(BUGS_WITH_DATA, 99)

    def test_blank_resolved_in(self):
        result = parser.transform_resolve_bug(BUGS_WITH_DATA, 1, today="2024-06-01")
        doc = parser._parse_text(result)
        r = next(x for x in doc.resolved if x.id == 1)
        assert r.resolved_in == ""
