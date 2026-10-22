import httpx
import pytest

from app import hub_identity as hub
from app import scope
from app.registry import run_git
from tests.test_project_scope import origin_of, program, remote_text  # noqa: F401

HUB_URL = "http://hub/revops/internal/auth?app=strategy"


@pytest.fixture()
def hub_mode(monkeypatch):
    """Hub identity on, with a fake hub: cookie 'sid=<role>:<email>' -> that user, 'sid=none' -> 403."""
    monkeypatch.setattr(type(hub.settings), "hub_auth_url", property(lambda self: HUB_URL))
    hub._cache.clear()
    calls = []

    class FakeAsyncClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, headers=None):
            calls.append((url, headers))
            cookie = (headers or {}).get("cookie", "")
            if cookie == "sid=boom":
                raise httpx.ConnectError("down")
            if cookie == "sid=none":
                return httpx.Response(403)
            if cookie.startswith("sid=") and ":" in cookie:
                role, email = cookie[4:].split(":", 1)
                return httpx.Response(200, headers={"X-User-Email": email, "X-App-Role": role})
            return httpx.Response(401)

    monkeypatch.setattr(hub.httpx, "AsyncClient", FakeAsyncClient)
    yield calls
    hub._cache.clear()


def as_user(client, role, email="ada@example.com"):
    client.cookies.clear()
    client.cookies.set("sid", f"{role}:{email}")
    return client


def test_no_cookie_is_sent_to_sign_in_and_no_grant_is_forbidden(client, program, hub_mode):
    client.cookies.clear()
    r = client.get("/p/alpha/dashboard", follow_redirects=False)
    assert r.status_code == 303
    client.cookies.set("sid", "none")
    assert client.get("/p/alpha/dashboard").status_code == 403


def test_hub_outage_fails_closed(client, program, hub_mode):
    client.cookies.set("sid", "boom")
    assert client.get("/p/alpha/dashboard").status_code == 503


def test_viewer_reads_but_cannot_write_or_triage(client, program, hub_mode):
    as_user(client, "viewer")
    assert client.get("/p/alpha/features").status_code == 200
    assert client.get("/p").status_code == 200
    assert client.patch("/p/alpha/api/features/1.1.2", json={"notes": "x"}).status_code == 403
    assert client.post("/p/alpha/api/inbox", json={"type": "bug", "title": "x"}).status_code == 403
    assert client.post("/p/alpha/api/markdown/preview", json={"content": "# hi"}).status_code == 200   # a read


def test_editor_writes_and_is_the_commit_author_not_a_header(client, program, hub_mode):
    as_user(client, "editor", "grace@example.com")
    r = client.patch("/p/alpha/api/features/1.1.2", json={"notes": "By grace"},
                     headers={"X-Host-User": "mallory@example.com", "X-User-Email": "mallory@example.com"})
    assert r.status_code == 200, r.text
    assert "By grace" in remote_text(program, "alpha", "PRODUCT.MD")
    assert run_git(origin_of(program, "alpha"), ["log", "-1", "--format=%ae", "main"]) == "grace@example.com"


def test_admin_may_write_too(client, program, hub_mode):
    as_user(client, "admin")
    assert client.patch("/p/alpha/api/features/1.1.2", json={"notes": "adm"}).status_code == 200


def test_verdict_is_cached_briefly_per_cookie(client, program, hub_mode):
    as_user(client, "viewer")
    client.get("/p/alpha/dashboard")
    client.get("/p/alpha/features")
    assert len(hub_mode) == 1
    as_user(client, "viewer", "other@example.com")
    client.get("/p/alpha/dashboard")
    assert len(hub_mode) == 2


def test_hub_answers_with_an_unknown_role_are_refused(client, program, hub_mode):
    as_user(client, "superuser")
    assert client.get("/p/alpha/dashboard").status_code == 403


def test_is_write_classification():
    assert not hub.is_write("GET", "/p/a/api/features")
    assert hub.is_write("PATCH", "/p/a/api/features/1.1.1")
    assert not hub.is_write("POST", "/p/a/api/markdown/preview")
    assert hub.is_write("POST", "/api/inbox")
