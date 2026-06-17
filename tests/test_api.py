"""
API integration tests using FastAPI TestClient.
Auth is disabled (figtion defaults enabled=False) so no login needed.
"""
import pytest
import app.auth as auth_mod


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(auth_mod, "cfg", {
        "enabled": False,
        "username": "admin",
        "password": "changeme",
        "secret_key": "a" * 64,
    })


class TestProductEndpoint:
    def test_get_product_returns_200(self, client):
        r = client.get("/api/product")
        assert r.status_code == 200

    def test_get_product_has_features(self, client):
        data = client.get("/api/product").json()
        wbs_areas = data["wbs_areas"]
        assert len(wbs_areas) > 0
        all_features = [f for area in wbs_areas for sa in area["sub_areas"] for f in sa["features"]]
        assert len(all_features) > 0

    def test_get_product_title(self, client):
        data = client.get("/api/product").json()
        assert "Test Product" in data["title"]


class TestAboutEndpoint:
    def test_get_about_returns_200(self, client):
        r = client.get("/api/about")
        assert r.status_code == 200

    def test_get_about_has_changelog(self, client):
        data = client.get("/api/about").json()
        assert len(data["changelog"]) > 0

    def test_get_about_has_roadmap(self, client):
        data = client.get("/api/about").json()
        section_names = [s["name"] for s in data["roadmap"]]
        assert "In Progress" in section_names


class TestPatchFeature:
    def test_patch_status(self, client):
        r = client.patch("/api/features/1.1.2", json={"status": "Scored"})
        assert r.status_code == 200
        data = client.get("/api/product").json()
        all_features = [
            f for area in data["wbs_areas"]
            for sa in area["sub_areas"]
            for f in sa["features"]
        ]
        f = next(x for x in all_features if x["wbs"] == "1.1.2")
        assert f["status"] == "Scored"

    def test_patch_name(self, client):
        r = client.patch("/api/features/1.1.2", json={"name": "Sign Out"})
        assert r.status_code == 200

    def test_patch_notes(self, client):
        r = client.patch("/api/features/1.1.2", json={"notes": "Updated note"})
        assert r.status_code == 200

    def test_patch_score_sets_scored_status(self, client):
        r = client.patch("/api/features/1.1.2", json={"value": 7, "effort": 3})
        assert r.status_code == 200
        data = client.get("/api/product").json()
        all_features = [
            f for area in data["wbs_areas"]
            for sa in area["sub_areas"]
            for f in sa["features"]
        ]
        f = next(x for x in all_features if x["wbs"] == "1.1.2")
        assert f["status"] == "Scored"

    def test_patch_clear_score_reverts_to_scoped(self, client):
        client.patch("/api/features/1.1.2", json={"value": 7, "effort": 3})
        r = client.patch("/api/features/1.1.2", json={"value": None, "effort": None})
        assert r.status_code == 200
        data = client.get("/api/product").json()
        all_features = [
            f for area in data["wbs_areas"]
            for sa in area["sub_areas"]
            for f in sa["features"]
        ]
        f = next(x for x in all_features if x["wbs"] == "1.1.2")
        assert f["status"] == "Scoped"

    def test_patch_unknown_wbs_returns_404(self, client):
        r = client.patch("/api/features/9.9.9", json={"status": "Live"})
        assert r.status_code == 404


class TestCreateFeature:
    def test_creates_feature(self, client):
        r = client.post("/api/features", json={
            "wbs_prefix": "1.1",
            "name": "MFA",
            "status": "Idea",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "MFA"
        assert data["wbs"].startswith("1.1.")

    def test_creates_with_score(self, client):
        r = client.post("/api/features", json={
            "wbs_prefix": "1.2",
            "name": "Charts",
            "status": "Scored",
            "value": 8,
            "effort": 3,
        })
        assert r.status_code == 200
        assert r.json()["wbs"].startswith("1.2.")

    def test_create_unknown_prefix_returns_error(self, client):
        r = client.post("/api/features", json={
            "wbs_prefix": "9.9",
            "name": "Ghost",
        })
        assert r.status_code in (400, 404)


class TestBugEndpoints:
    def test_get_bugs_returns_200(self, client):
        r = client.get("/api/bugs")
        assert r.status_code == 200

    def test_create_bug(self, client):
        r = client.post("/api/bugs", json={"title": "Test bug", "severity": "High"})
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Test bug"
        assert data["id"] >= 1

    def test_update_bug_status(self, client):
        r = client.post("/api/bugs", json={"title": "Fix me"})
        bug_id = r.json()["id"]
        r2 = client.patch(f"/api/bugs/{bug_id}", json={"status": "Investigating"})
        assert r2.status_code == 200
        assert r2.json()["status"] == "Investigating"

    def test_resolve_bug(self, client):
        r = client.post("/api/bugs", json={"title": "Resolve me"})
        bug_id = r.json()["id"]
        r2 = client.post(f"/api/bugs/{bug_id}/resolve", json={"resolved_in": "0.2.0"})
        assert r2.status_code == 200
        bugs = client.get("/api/bugs").json()
        # Active row stays with Resolved status; bug also appears in resolved table
        active_statuses = {b["id"]: b["status"] for b in bugs["active"]}
        resolved_ids = [b["id"] for b in bugs["resolved"]]
        assert active_statuses.get(bug_id) == "Resolved"
        assert bug_id in resolved_ids

    def test_update_nonexistent_bug(self, client):
        r = client.patch("/api/bugs/9999", json={"title": "Ghost"})
        assert r.status_code == 404


class TestPageRoutes:
    def test_dashboard(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert b"Test Product" in r.content

    def test_features_page(self, client):
        r = client.get("/features")
        assert r.status_code == 200

    def test_roadmap_page(self, client):
        r = client.get("/roadmap")
        assert r.status_code == 200

    def test_registry_page(self, client):
        r = client.get("/registry")
        assert r.status_code == 200

    def test_about_page(self, client):
        r = client.get("/about")
        assert r.status_code == 200

    def test_bugs_page(self, client):
        r = client.get("/bugs")
        assert r.status_code == 200

    def test_root_redirects_to_dashboard(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (301, 302, 307, 308)
        assert "/dashboard" in r.headers["location"]


class TestAuthRoutes:
    def test_login_page_renders(self, client):
        r = client.get("/login")
        assert r.status_code == 200
        assert b"Sign in" in r.content or b"sign in" in r.content.lower()

    def test_login_with_valid_credentials(self, client, monkeypatch):
        monkeypatch.setattr(auth_mod, "cfg", {
            "enabled": True,
            "username": "admin",
            "password": "secret",
            "secret_key": "a" * 64,
        })
        r = client.post("/login", data={"username": "admin", "password": "secret", "next": "/dashboard"},
                        follow_redirects=False)
        assert r.status_code == 303
        assert auth_mod.COOKIE_NAME in r.cookies

    def test_login_with_bad_credentials(self, client, monkeypatch):
        monkeypatch.setattr(auth_mod, "cfg", {
            "enabled": True,
            "username": "admin",
            "password": "secret",
            "secret_key": "a" * 64,
        })
        r = client.post("/login", data={"username": "admin", "password": "wrong", "next": "/dashboard"})
        assert r.status_code == 401

    def test_logout_clears_cookie(self, client, monkeypatch):
        monkeypatch.setattr(auth_mod, "cfg", {
            "enabled": True,
            "username": "admin",
            "password": "secret",
            "secret_key": "a" * 64,
        })
        client.post("/login", data={"username": "admin", "password": "secret", "next": "/dashboard"})
        r = client.get("/logout", follow_redirects=False)
        assert r.status_code == 303
