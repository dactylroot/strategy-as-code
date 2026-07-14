import httpx
import pytest

from app import github_issues
from app.models import BugItem, BugSeverity, BugStatus


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
