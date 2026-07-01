import asyncio

import httpx
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

        assert asyncio.run(auth_mod.is_authenticated(FakeRequest())) is True

    def test_valid_cookie_passes(self):
        cookie = auth_mod.make_cookie("admin")

        class FakeRequest:
            cookies = {auth_mod.COOKIE_NAME: cookie}

        assert asyncio.run(auth_mod.is_authenticated(FakeRequest())) is True

    def test_missing_cookie_fails(self):
        class FakeRequest:
            cookies = {}

        assert asyncio.run(auth_mod.is_authenticated(FakeRequest())) is False

    def test_bad_cookie_fails(self):
        class FakeRequest:
            cookies = {auth_mod.COOKIE_NAME: "garbage"}

        assert asyncio.run(auth_mod.is_authenticated(FakeRequest())) is False


class TestUpstreamSessionPassthrough:
    """Auth-passthrough mode: strategy-as-code trusts the host project's own
    session instead of checking the local pac_auth cookie."""

    class FakeRequest:
        def __init__(self, cookie_header=None):
            self.headers = {"cookie": cookie_header} if cookie_header else {}

    def _patch_introspect_url(self, monkeypatch, url):
        monkeypatch.setattr(type(auth_mod.settings), "auth_introspect_url", property(lambda self: url))

    def _patch_client(self, monkeypatch, handler):
        class FakeAsyncClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                return handler(url, headers)

        monkeypatch.setattr(auth_mod.httpx, "AsyncClient", FakeAsyncClient)

    def test_no_cookie_header_fails_without_network_call(self, monkeypatch):
        self._patch_introspect_url(monkeypatch, "http://host/whoami")
        assert asyncio.run(auth_mod.is_authenticated(self.FakeRequest())) is False

    def test_upstream_200_passes(self, monkeypatch):
        self._patch_introspect_url(monkeypatch, "http://host/whoami")
        self._patch_client(monkeypatch, lambda url, headers: httpx.Response(200))
        assert asyncio.run(auth_mod.is_authenticated(self.FakeRequest("session=abc"))) is True

    def test_upstream_401_fails(self, monkeypatch):
        self._patch_introspect_url(monkeypatch, "http://host/whoami")
        self._patch_client(monkeypatch, lambda url, headers: httpx.Response(401))
        assert asyncio.run(auth_mod.is_authenticated(self.FakeRequest("session=abc"))) is False

    def test_upstream_network_error_fails_closed(self, monkeypatch):
        self._patch_introspect_url(monkeypatch, "http://host/whoami")

        def _raise(url, headers):
            raise httpx.ConnectError("boom")

        self._patch_client(monkeypatch, _raise)
        assert asyncio.run(auth_mod.is_authenticated(self.FakeRequest("session=abc"))) is False

    def test_local_cookie_ignored_when_introspect_url_set(self, monkeypatch):
        # Even a valid local cookie shouldn't pass once passthrough mode is on.
        self._patch_introspect_url(monkeypatch, "http://host/whoami")
        self._patch_client(monkeypatch, lambda url, headers: httpx.Response(401))
        cookie = auth_mod.make_cookie("admin")
        request = self.FakeRequest(f"{auth_mod.COOKIE_NAME}={cookie}")
        assert asyncio.run(auth_mod.is_authenticated(request)) is False
