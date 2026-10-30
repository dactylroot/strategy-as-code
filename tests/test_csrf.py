"""Writes to the API must come from this site's own pages (hub mode): see hub_identity.refuse_cross_site_writes."""
import pytest

from app import hub_identity as hub
from tests.test_hub_identity import as_user, hub_mode  # noqa: F401
from tests.test_project_scope import program  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
CROSS = {"Sec-Fetch-Site": "cross-site"}
SAME_SITE = {"Sec-Fetch-Site": "same-site"}
OWN = {"Sec-Fetch-Site": "same-origin"}


def mine(client, email="ada@example.com", role="contributor"):
    as_user(client, role, email)
    r = client.post("/p/alpha/api/inbox", json={"type": "bug", "title": "T"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.parametrize("headers", [CROSS, SAME_SITE], ids=["cross-site", "same-site"])
def test_a_write_from_another_site_is_refused_whatever_it_carries(client, program, hub_mode, headers):  # noqa: F811
    item = mine(client)
    for method, url, kw in (
        ("POST", "/p/alpha/api/inbox", dict(json={"type": "bug", "title": "forged"})),
        ("POST", f"/p/alpha/api/inbox/{item}/screenshot", dict(files={"file": ("a.png", PNG, "image/png")})),
        ("PATCH", f"/p/alpha/api/inbox/{item}", dict(json={"title": "forged"})),
        ("DELETE", f"/p/alpha/api/inbox/{item}", {}),
    ):
        r = client.request(method, url, headers=headers, **kw)
        assert r.status_code == 403 and "Cross-site" in r.json()["detail"], (method, url)
    assert [i["title"] for i in client.get("/p/alpha/api/inbox/mine").json()] == ["T"]   # nothing was written


@pytest.mark.parametrize("headers", [CROSS, SAME_SITE], ids=["cross-site", "same-site"])
def test_the_editor_endpoints_a_plain_form_could_reach_are_closed_too(client, program, hub_mode, headers):  # noqa: F811
    as_user(client, "editor", "grace@example.com")
    client.get("/p/alpha/bugs")
    for url in ("/p/alpha/api/bugs/1/close", "/p/alpha/api/wbs/regenerate", "/p/alpha/api/switch-project"):
        r = client.post(url, headers=headers, content=b"", )
        assert r.status_code == 403 and "Cross-site" in r.json()["detail"], url
    assert client.post("/p/alpha/api/bugs/1/close", headers=OWN).status_code != 403     # the page's own request still works


def test_the_pages_own_requests_and_typed_addresses_work(client, program, hub_mode):  # noqa: F811
    as_user(client, "contributor", "ada@example.com")
    for headers in (OWN, {"Sec-Fetch-Site": "none"}):
        r = client.post("/p/alpha/api/inbox", json={"type": "bug", "title": "T"}, headers=headers)
        assert r.status_code == 201, headers


def test_a_request_with_no_browser_headers_is_no_browser_and_is_let_through(client, program, hub_mode):  # noqa: F811
    mine(client)                                                                         # TestClient sends neither header


def test_reads_are_never_refused_for_where_they_come_from(client, program, hub_mode):  # noqa: F811
    as_user(client, "contributor", "ada@example.com")
    assert client.get("/p/alpha/api/inbox/mine", headers=CROSS).status_code == 200
    assert client.get("/p/alpha/features", headers=CROSS).status_code == 200


def test_an_old_browser_without_fetch_metadata_is_judged_by_its_origin(client, program, hub_mode, monkeypatch):  # noqa: F811
    monkeypatch.setattr(type(hub.settings), "host_home_url", property(lambda self: "https://host.example/revops/"))
    as_user(client, "contributor", "ada@example.com")
    post = lambda origin: client.post("/p/alpha/api/inbox", json={"type": "bug", "title": "T"}, headers={"Origin": origin})
    assert post("https://evil.example").status_code == 403
    assert post("https://host.example.evil.example").status_code == 403
    assert post("null").status_code == 403
    assert post("https://host.example").status_code == 201          # the host's own origin, behind the proxy
    assert post("http://testserver").status_code == 201             # this server's own origin, reached directly


def test_fetch_metadata_wins_over_a_matching_origin(client, program, hub_mode):  # noqa: F811
    as_user(client, "contributor", "ada@example.com")
    r = client.post("/p/alpha/api/inbox", json={"type": "bug", "title": "T"},
                    headers={"Sec-Fetch-Site": "cross-site", "Origin": "http://testserver"})
    assert r.status_code == 403


def test_the_signed_webhook_does_not_depend_on_it(client, program, hub_mode, monkeypatch):  # noqa: F811
    r = client.post("/hooks/refresh", headers=CROSS, content=b"{}")
    assert r.status_code in (401, 404)                               # its own signature check, not a cross-site refusal


def test_outside_hub_mode_nothing_changes(client, program):  # noqa: F811
    assert not hub.enabled()
    r = client.post("/p/alpha/api/markdown/preview", json={"content": "# hi"}, headers=CROSS)
    assert r.status_code == 200
