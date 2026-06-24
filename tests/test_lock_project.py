"""
Tests for lock_project: server.yml deploy config, locked-mode page, and API guard.
"""
import pytest
import app.auth as auth_mod
import app.config as cfg_mod

_UNLOCKED_DEPLOY = {"project_dir": "", "lock_project": False, "app_title": ""}
_LOCKED_DEPLOY   = {"project_dir": "", "lock_project": True,  "app_title": ""}


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(auth_mod, "cfg", {
        "enabled": False, "username": "admin",
        "password": "changeme", "secret_key": "a" * 64,
    })


class TestLockProjectSetting:
    def test_unlocked_by_default(self, monkeypatch):
        monkeypatch.setattr(cfg_mod, "_deploy", _UNLOCKED_DEPLOY)
        assert cfg_mod.settings.lock_project is False

    def test_locked_when_deploy_flag_set(self, monkeypatch):
        monkeypatch.setattr(cfg_mod, "_deploy", _LOCKED_DEPLOY)
        assert cfg_mod.settings.lock_project is True


class TestSwitchProjectPage:
    def test_unlocked_shows_full_ui(self, client, monkeypatch):
        monkeypatch.setattr(cfg_mod, "_deploy", _UNLOCKED_DEPLOY)
        r = client.get("/switch-project")
        assert r.status_code == 200
        assert "Switch Project" in r.text
        assert "Local Project" in r.text
        assert "Import Files" in r.text

    def test_locked_shows_download_only(self, client, monkeypatch):
        monkeypatch.setattr(cfg_mod, "_deploy", _LOCKED_DEPLOY)
        r = client.get("/switch-project")
        assert r.status_code == 200
        assert "Switch Project" not in r.text
        assert "Local Project" not in r.text
        assert "Download project files" in r.text

    def test_locked_does_not_expose_path(self, client, monkeypatch):
        monkeypatch.setattr(cfg_mod, "_deploy", _LOCKED_DEPLOY)
        r = client.get("/switch-project")
        # project_dir path should not appear in the locked page
        assert str(cfg_mod.settings.project_dir) not in r.text


class TestSwitchProjectApi:
    def test_unlocked_rejects_missing_product_md(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(cfg_mod, "_deploy", _UNLOCKED_DEPLOY)
        empty = tmp_path / "no_product"
        empty.mkdir()
        r = client.post("/api/switch-project", json={"project_dir": str(empty)})
        assert r.status_code == 400
        assert "PRODUCT.MD" in r.json()["detail"]

    def test_locked_returns_403(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(cfg_mod, "_deploy", _LOCKED_DEPLOY)
        (tmp_path / "PRODUCT.MD").write_text("# Locked\n", encoding="utf-8")
        r = client.post("/api/switch-project", json={"project_dir": str(tmp_path)})
        assert r.status_code == 403
        assert "disabled" in r.json()["detail"].lower()
