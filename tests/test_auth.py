import pytest
import app.auth as auth_mod


_FAKE_CFG = {
    "enabled": True,
    "username": "admin",
    "password": "s3cr3t",
    "secret_key": "a" * 64,
}


@pytest.fixture(autouse=True)
def patch_cfg(monkeypatch):
    monkeypatch.setattr(auth_mod, "cfg", _FAKE_CFG)


class TestEnabled:
    def test_enabled_true(self):
        assert auth_mod.enabled() is True

    def test_enabled_false(self, monkeypatch):
        monkeypatch.setattr(auth_mod, "cfg", {**_FAKE_CFG, "enabled": False})
        assert auth_mod.enabled() is False


class TestCheckCredentials:
    def test_valid(self):
        assert auth_mod.check_credentials("admin", "s3cr3t") is True

    def test_wrong_password(self):
        assert auth_mod.check_credentials("admin", "wrong") is False

    def test_wrong_username(self):
        assert auth_mod.check_credentials("notadmin", "s3cr3t") is False

    def test_both_wrong(self):
        assert auth_mod.check_credentials("x", "y") is False

    def test_empty_credentials(self):
        assert auth_mod.check_credentials("", "") is False


class TestCookieRoundTrip:
    def test_make_and_verify(self):
        cookie = auth_mod.make_cookie("admin")
        assert auth_mod.verify_cookie(cookie) == "admin"

    def test_different_usernames(self):
        for username in ("admin", "alice", "user_123"):
            cookie = auth_mod.make_cookie(username)
            assert auth_mod.verify_cookie(cookie) == username

    def test_tampered_signature_rejected(self):
        cookie = auth_mod.make_cookie("admin")
        username, sig = cookie.rsplit(":", 1)
        tampered = f"{username}:{sig[:-4]}ffff"
        assert auth_mod.verify_cookie(tampered) is None

    def test_tampered_username_rejected(self):
        cookie = auth_mod.make_cookie("admin")
        _, sig = cookie.rsplit(":", 1)
        assert auth_mod.verify_cookie(f"attacker:{sig}") is None

    def test_garbage_value(self):
        assert auth_mod.verify_cookie("notavalidcookie") is None

    def test_empty_string(self):
        assert auth_mod.verify_cookie("") is None

    def test_wrong_secret_key_rejected(self):
        cookie = auth_mod.make_cookie("admin")
        # Swap in a different secret key
        import app.auth as a
        original_cfg = a.cfg
        try:
            a.cfg = {**_FAKE_CFG, "secret_key": "b" * 64}
            assert a.verify_cookie(cookie) is None
        finally:
            a.cfg = original_cfg


class TestIsAuthenticated:
    def test_auth_disabled_always_passes(self, monkeypatch):
        monkeypatch.setattr(auth_mod, "cfg", {**_FAKE_CFG, "enabled": False})

        class FakeRequest:
            cookies = {}

        assert auth_mod.is_authenticated(FakeRequest()) is True

    def test_valid_cookie_passes(self):
        cookie = auth_mod.make_cookie("admin")

        class FakeRequest:
            cookies = {auth_mod.COOKIE_NAME: cookie}

        assert auth_mod.is_authenticated(FakeRequest()) is True

    def test_missing_cookie_fails(self):
        class FakeRequest:
            cookies = {}

        assert auth_mod.is_authenticated(FakeRequest()) is False

    def test_bad_cookie_fails(self):
        class FakeRequest:
            cookies = {auth_mod.COOKIE_NAME: "garbage"}

        assert auth_mod.is_authenticated(FakeRequest()) is False
