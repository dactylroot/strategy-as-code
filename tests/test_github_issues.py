import httpx
import pytest

from app import github_issues
from app.models import BugItem, BugSeverity, BugStatus
from app.parsers import bugs as bugs_parser


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


@pytest.fixture(autouse=True)
def _configure(monkeypatch, tmp_path):
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_runtime_project_dir", tmp_path)
    monkeypatch.setattr(cfg_mod._base, "github_repo", "acme/widgets")
    monkeypatch.setattr(cfg_mod._base, "git_token", "test-token")
    return tmp_path


def make_bug(**kwargs):
    defaults = dict(id=1, title="Something broke", severity=BugSeverity.medium,
                     status=BugStatus.open, notes="It broke.")
    defaults.update(kwargs)
    return BugItem(**defaults)


class TestFindScreenshot:
    def test_finds_matching_file(self, tmp_path):
        d = tmp_path / ".screenshots"
        d.mkdir()
        (d / "bug_7.png").write_bytes(b"fake-png")
        assert github_issues._find_screenshot(7) == d / "bug_7.png"

    def test_none_when_absent(self, tmp_path):
        assert github_issues._find_screenshot(7) is None


class TestCreateIssueBody:
    def test_includes_reporter_and_wbs(self, monkeypatch):
        bug = make_bug(owner="Jane Doe", wbs_ref="1.2.3")
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["payload"] = json
            return _FakeResponse(201, {"number": 42})

        monkeypatch.setattr(github_issues.httpx, "post", fake_post)
        ref = github_issues._create_issue(bug)

        assert ref == "42"
        assert "**Reported by:** Jane Doe" in captured["payload"]["body"]
        assert "**WBS:** 1.2.3" in captured["payload"]["body"]

    def test_omits_reporter_and_wbs_when_absent(self, monkeypatch):
        bug = make_bug()
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["payload"] = json
            return _FakeResponse(201, {"number": 5})

        monkeypatch.setattr(github_issues.httpx, "post", fake_post)
        github_issues._create_issue(bug)

        assert "Reported by" not in captured["payload"]["body"]
        assert "WBS" not in captured["payload"]["body"]

    def test_includes_screenshot_link_when_uploaded(self, monkeypatch, tmp_path):
        d = tmp_path / ".screenshots"
        d.mkdir()
        (d / "bug_1.png").write_bytes(b"fake-png")
        bug = make_bug(id=1)
        captured = {}

        monkeypatch.setattr(
            github_issues, "_upload_screenshot_asset",
            lambda bug_id, path: "https://github.com/acme/widgets/releases/download/bug-screenshots/bug-1.png",
        )

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["payload"] = json
            return _FakeResponse(201, {"number": 9})

        monkeypatch.setattr(github_issues.httpx, "post", fake_post)
        github_issues._create_issue(bug)

        assert "**Screenshot:**" in captured["payload"]["body"]
        assert "bug-screenshots/bug-1.png" in captured["payload"]["body"]

    def test_no_screenshot_section_when_upload_fails(self, monkeypatch, tmp_path):
        d = tmp_path / ".screenshots"
        d.mkdir()
        (d / "bug_1.png").write_bytes(b"fake-png")
        bug = make_bug(id=1)
        captured = {}

        monkeypatch.setattr(github_issues, "_upload_screenshot_asset", lambda bug_id, path: None)

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["payload"] = json
            return _FakeResponse(201, {"number": 9})

        monkeypatch.setattr(github_issues.httpx, "post", fake_post)
        github_issues._create_issue(bug)

        assert "Screenshot" not in captured["payload"]["body"]


class TestAssetReleaseId:
    def test_reuses_existing_release(self, monkeypatch):
        def fake_get(url, headers=None, timeout=None):
            assert url.endswith(f"/releases/tags/{github_issues._ASSET_RELEASE_TAG}")
            return _FakeResponse(200, {"id": 123})

        monkeypatch.setattr(github_issues.httpx, "get", fake_get)
        assert github_issues._asset_release_id() == 123

    def test_creates_release_when_missing(self, monkeypatch):
        def fake_get(url, headers=None, timeout=None):
            return _FakeResponse(404, {})

        def fake_post(url, json=None, headers=None, timeout=None):
            assert json["draft"] is True
            assert json["tag_name"] == github_issues._ASSET_RELEASE_TAG
            return _FakeResponse(201, {"id": 456})

        monkeypatch.setattr(github_issues.httpx, "get", fake_get)
        monkeypatch.setattr(github_issues.httpx, "post", fake_post)
        assert github_issues._asset_release_id() == 456


class TestUploadScreenshotAsset:
    def test_reuses_existing_asset_by_name(self, monkeypatch, tmp_path):
        path = tmp_path / "bug_3.png"
        path.write_bytes(b"fake-png")
        monkeypatch.setattr(github_issues, "_asset_release_id", lambda: 1)

        def fake_get(url, headers=None, timeout=None):
            return _FakeResponse(200, [{"name": "bug-3.png", "browser_download_url": "https://existing"}])

        posted = {"called": False}

        def fake_post(*a, **kw):
            posted["called"] = True
            return _FakeResponse(201, {"browser_download_url": "https://new"})

        monkeypatch.setattr(github_issues.httpx, "get", fake_get)
        monkeypatch.setattr(github_issues.httpx, "post", fake_post)

        url = github_issues._upload_screenshot_asset(3, path)
        assert url == "https://existing"
        assert posted["called"] is False

    def test_uploads_new_asset(self, monkeypatch, tmp_path):
        path = tmp_path / "bug_3.png"
        path.write_bytes(b"fake-png")
        monkeypatch.setattr(github_issues, "_asset_release_id", lambda: 1)

        def fake_get(url, headers=None, timeout=None):
            return _FakeResponse(200, [])

        def fake_post(url, params=None, content=None, headers=None, timeout=None):
            assert params["name"] == "bug-3.png"
            assert content == b"fake-png"
            return _FakeResponse(201, {"browser_download_url": "https://new"})

        monkeypatch.setattr(github_issues.httpx, "get", fake_get)
        monkeypatch.setattr(github_issues.httpx, "post", fake_post)

        url = github_issues._upload_screenshot_asset(3, path)
        assert url == "https://new"

    def test_returns_none_when_release_lookup_fails(self, monkeypatch, tmp_path):
        path = tmp_path / "bug_3.png"
        path.write_bytes(b"fake-png")
        monkeypatch.setattr(github_issues, "_asset_release_id", lambda: None)
        assert github_issues._upload_screenshot_asset(3, path) is None


class TestScanMirroredIssues:
    def test_matches_marker_in_body(self, monkeypatch):
        def fake_get(url, params=None, headers=None, timeout=None):
            if params["page"] == 1:
                return _FakeResponse(200, [
                    {"number": 45, "state": "open", "body": f"blah\n{github_issues._dedup_marker(3)}"},
                    {"number": 46, "state": "open", "body": "no marker here"},
                ])
            return _FakeResponse(200, [])

        monkeypatch.setattr(github_issues.httpx, "get", fake_get)
        assert github_issues._scan_mirrored_issues() == {3: {"number": "45", "state": "open"}}

    def test_captures_closed_state(self, monkeypatch):
        def fake_get(url, params=None, headers=None, timeout=None):
            if params["page"] == 1:
                return _FakeResponse(200, [
                    {"number": 45, "state": "closed", "body": github_issues._dedup_marker(3)},
                ])
            return _FakeResponse(200, [])

        monkeypatch.setattr(github_issues.httpx, "get", fake_get)
        assert github_issues._scan_mirrored_issues() == {3: {"number": "45", "state": "closed"}}

    def test_paginates_until_short_page(self, monkeypatch):
        pages = {
            1: [{"number": i, "state": "open", "body": github_issues._dedup_marker(i)} for i in range(100)],
            2: [{"number": 100, "state": "open", "body": github_issues._dedup_marker(100)}],
        }

        def fake_get(url, params=None, headers=None, timeout=None):
            return _FakeResponse(200, pages.get(params["page"], []))

        monkeypatch.setattr(github_issues.httpx, "get", fake_get)
        result = github_issues._scan_mirrored_issues()
        assert len(result) == 101
        assert result[100] == {"number": "100", "state": "open"}

    def test_returns_empty_on_request_failure(self, monkeypatch):
        def fake_get(url, params=None, headers=None, timeout=None):
            raise httpx.ConnectError("boom")

        monkeypatch.setattr(github_issues.httpx, "get", fake_get)
        assert github_issues._scan_mirrored_issues() == {}


class TestCreateMissingIssues:
    def _write_bugs_md(self, tmp_path, rows):
        header = (
            "# Bugs\n\n## Active\n\n"
            "| ID | Title | Severity | Status | Notes | WBS | Fix Version | Owner | UAT Confirmed | GH Issue |\n"
            "|----|-------|----------|--------|-------|-----|-------------|-------|----------------|----------|\n"
        )
        (tmp_path / "BUGS.MD").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")

    def test_backfills_from_existing_issue_without_creating(self, monkeypatch, tmp_path):
        self._write_bugs_md(tmp_path, ["| 1 | Broken thing | Medium | Open | It broke. |  |  |  |  |  |"])
        monkeypatch.setattr(
            github_issues, "_scan_mirrored_issues", lambda: {1: {"number": "45", "state": "open"}}
        )
        created = {"called": False}
        monkeypatch.setattr(
            github_issues, "_create_issue",
            lambda bug: created.__setitem__("called", True) or "999",
        )

        github_issues.create_missing_issues()

        assert created["called"] is False
        doc = bugs_parser.parse(github_issues.settings.bugs_md)
        assert doc.active[0].gh_issue == "45"

    def test_creates_issue_when_none_exists_yet(self, monkeypatch, tmp_path):
        self._write_bugs_md(tmp_path, ["| 1 | Broken thing | Medium | Open | It broke. |  |  |  |  |  |"])
        monkeypatch.setattr(github_issues, "_scan_mirrored_issues", lambda: {})
        monkeypatch.setattr(github_issues, "_create_issue", lambda bug: "77")

        github_issues.create_missing_issues()

        doc = bugs_parser.parse(github_issues.settings.bugs_md)
        assert doc.active[0].gh_issue == "77"

    def test_skips_bugs_already_linked(self, monkeypatch, tmp_path):
        self._write_bugs_md(tmp_path, ["| 1 | Broken thing | Medium | Open | It broke. |  |  |  | | 12 |"])
        scan_called = {"called": False}
        monkeypatch.setattr(
            github_issues, "_scan_mirrored_issues",
            lambda: scan_called.__setitem__("called", True) or {},
        )

        github_issues.create_missing_issues()

        assert scan_called["called"] is False

    def test_embeds_dedup_marker_in_created_issue_body(self, monkeypatch):
        bug = make_bug(id=8)
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["payload"] = json
            return _FakeResponse(201, {"number": 1})

        monkeypatch.setattr(github_issues.httpx, "post", fake_post)
        github_issues._create_issue(bug)

        assert github_issues._dedup_marker(8) in captured["payload"]["body"]


class TestCloseResolvedIssues:
    def _write_bugs_md(self, tmp_path, active_rows=(), closed_rows=()):
        text = (
            "# Bugs\n\n## Active\n\n"
            "| ID | Title | Severity | Status | Notes | WBS | Fix Version | Owner | UAT Confirmed | GH Issue |\n"
            "|----|-------|----------|--------|-------|-----|-------------|-------|----------------|----------|\n"
            + "\n".join(active_rows) + "\n\n"
            "## Closed\n\n"
            "| ID | Title | Resolved In | Date | GH Issue |\n"
            "|----|-------|-------------|------|----------|\n"
            + "\n".join(closed_rows) + "\n"
        )
        (tmp_path / "BUGS.MD").write_text(text, encoding="utf-8")

    def test_closes_issue_for_resolved_bug(self, monkeypatch, tmp_path):
        self._write_bugs_md(tmp_path, closed_rows=["| 1 | Broken thing | v1.2 | 2026-07-14 | 45 |"])
        monkeypatch.setattr(github_issues, "_scan_mirrored_issues", lambda: {1: {"number": "45", "state": "open"}})
        closed = {}
        monkeypatch.setattr(
            github_issues, "_close_issue",
            lambda number, bug_id: closed.update(number=number, bug_id=bug_id),
        )

        github_issues.close_resolved_issues()

        assert closed == {"number": "45", "bug_id": 1}

    def test_skips_when_already_closed(self, monkeypatch, tmp_path):
        self._write_bugs_md(tmp_path, closed_rows=["| 1 | Broken thing | v1.2 | 2026-07-14 | 45 |"])
        monkeypatch.setattr(github_issues, "_scan_mirrored_issues", lambda: {1: {"number": "45", "state": "closed"}})
        called = {"called": False}
        monkeypatch.setattr(
            github_issues, "_close_issue",
            lambda number, bug_id: called.__setitem__("called", True),
        )

        github_issues.close_resolved_issues()

        assert called["called"] is False

    def test_falls_back_to_bugs_md_number_when_marker_not_found(self, monkeypatch, tmp_path):
        self._write_bugs_md(tmp_path, closed_rows=["| 1 | Broken thing | v1.2 | 2026-07-14 | 45 |"])
        monkeypatch.setattr(github_issues, "_scan_mirrored_issues", lambda: {})
        closed = {}
        monkeypatch.setattr(
            github_issues, "_close_issue",
            lambda number, bug_id: closed.update(number=number, bug_id=bug_id),
        )

        github_issues.close_resolved_issues()

        assert closed == {"number": "45", "bug_id": 1}

    def test_skips_resolved_bugs_without_linked_issue(self, monkeypatch, tmp_path):
        self._write_bugs_md(tmp_path, closed_rows=["| 1 | Broken thing | v1.2 | 2026-07-14 |  |"])
        scan_called = {"called": False}
        monkeypatch.setattr(
            github_issues, "_scan_mirrored_issues",
            lambda: scan_called.__setitem__("called", True) or {},
        )

        github_issues.close_resolved_issues()

        assert scan_called["called"] is False

    def test_patches_issue_state_to_closed(self, monkeypatch):
        captured = {}

        def fake_patch(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            return _FakeResponse(200, {})

        monkeypatch.setattr(github_issues.httpx, "patch", fake_patch)
        github_issues._close_issue("45", 1)

        assert captured["url"].endswith("/issues/45")
        assert captured["payload"]["state"] == "closed"

    def test_logs_and_swallows_error_on_patch_failure(self, monkeypatch):
        def fake_patch(url, json=None, headers=None, timeout=None):
            raise httpx.ConnectError("boom")

        monkeypatch.setattr(github_issues.httpx, "patch", fake_patch)
        github_issues._close_issue("45", 1)  # must not raise
