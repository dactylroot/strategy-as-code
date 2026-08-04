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
        r = client.patch("/api/features/1.1.2", json={"status": "In-Progress"})
        assert r.status_code == 200
        data = client.get("/api/product").json()
        all_features = [
            f for area in data["wbs_areas"]
            for sa in area["sub_areas"]
            for f in sa["features"]
        ]
        f = next(x for x in all_features if x["wbs"] == "1.1.2")
        assert f["status"] == "In-Progress"

    def test_patch_scored_status_rejected(self, client):
        """Scored is derived, not settable - PATCHing it directly
        is a validation error, not a status transition."""
        r = client.patch("/api/features/1.1.2", json={"status": "Scored"})
        assert r.status_code == 422

    def test_patch_name(self, client):
        r = client.patch("/api/features/1.1.2", json={"name": "Sign Out"})
        assert r.status_code == 200

    def test_patch_notes(self, client):
        r = client.patch("/api/features/1.1.2", json={"notes": "Updated note"})
        assert r.status_code == 200

    def test_patch_score_sets_scored_stage(self, client):
        # Setting a Value score is enough to derive Scored - no status write,
        # and notes have no bearing on the stage.
        r = client.patch("/api/features/1.1.2", json={"value": 7, "effort": 3})
        assert r.status_code == 200
        data = client.get("/api/product").json()
        all_features = [
            f for area in data["wbs_areas"]
            for sa in area["sub_areas"]
            for f in sa["features"]
        ]
        f = next(x for x in all_features if x["wbs"] == "1.1.2")
        assert f["status"] == "Idea"
        assert f["stage"] == "Scored"

    def test_patch_score_value_only_defaults_effort_and_scores(self, client):
        # No effort provided - priority_score and stage should still work,
        # using a default effort of 5.
        r = client.patch("/api/features/1.1.2", json={"value": 8})
        assert r.status_code == 200
        data = client.get("/api/product").json()
        all_features = [
            f for area in data["wbs_areas"]
            for sa in area["sub_areas"]
            for f in sa["features"]
        ]
        f = next(x for x in all_features if x["wbs"] == "1.1.2")
        assert f["stage"] == "Scored"
        assert f["priority_score"] == pytest.approx(8 / 5)

    def test_patch_clear_score_reverts_to_idea_stage(self, client):
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
        assert f["status"] == "Idea"
        assert f["stage"] == "Idea"  # notes alone don't keep it out of Ideas

    def test_patch_unknown_wbs_returns_404(self, client):
        r = client.patch("/api/features/9.9.9", json={"status": "Live"})
        assert r.status_code == 404

    def _get_feature(self, client, wbs):
        data = client.get("/api/product").json()
        all_features = [
            f for area in data["wbs_areas"]
            for sa in area["sub_areas"]
            for f in sa["features"]
        ]
        return next(x for x in all_features if x["wbs"] == wbs)

    def test_patch_status_to_live_clears_flag(self, client):
        # A gap flag means "needs attention" - once a feature ships, that
        # concern is resolved, so completing it should auto-clear the flag.
        client.patch("/api/features/1.1.2", json={"flagged": True})
        assert self._get_feature(client, "1.1.2")["flagged"] is True
        r = client.patch("/api/features/1.1.2", json={"status": "Live"})
        assert r.status_code == 200
        assert self._get_feature(client, "1.1.2")["flagged"] is False

    def test_patch_status_to_released_clears_flag(self, client):
        client.patch("/api/features/1.1.2", json={"flagged": True})
        r = client.patch("/api/features/1.1.2", json={"status": "Released"})
        assert r.status_code == 200
        assert self._get_feature(client, "1.1.2")["flagged"] is False

    def test_patch_status_to_in_progress_does_not_clear_flag(self, client):
        # Only Live/Released ("completed") auto-clear the flag - an
        # in-progress feature can still have an open concern.
        client.patch("/api/features/1.1.2", json={"flagged": True})
        r = client.patch("/api/features/1.1.2", json={"status": "In-Progress"})
        assert r.status_code == 200
        assert self._get_feature(client, "1.1.2")["flagged"] is True

    def test_patch_status_live_with_explicit_flag_respects_explicit_value(self, client):
        # If the same request explicitly sets flagged, that wins over the
        # auto-clear (e.g. re-flagging on the way to Released for a new concern).
        r = client.patch("/api/features/1.1.2", json={"status": "Live", "flagged": True})
        assert r.status_code == 200
        assert self._get_feature(client, "1.1.2")["flagged"] is True


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
            "status": "Idea",
            "value": 8,
            "effort": 3,
            "notes": "A real description",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["wbs"].startswith("1.2.")
        assert data["stage"] == "Scored"

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

    def test_close_bug(self, client):
        r = client.post("/api/bugs", json={"title": "Close me"})
        bug_id = r.json()["id"]
        r2 = client.post(f"/api/bugs/{bug_id}/close", json={"resolved_in": "0.2.0"})
        assert r2.status_code == 200
        bugs = client.get("/api/bugs").json()
        # Closing removes the bug from the active board; it lives only in the
        # Closed table afterwards.
        active_ids = [b["id"] for b in bugs["active"]]
        closed_ids = [b["id"] for b in bugs["closed"]]
        assert bug_id not in active_ids
        assert bug_id in closed_ids

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

    def test_registry_page_redirects_to_dashboard(self, client):
        # Registry page was removed - its content folded into the Feature
        # Registry section at the bottom of the Summary/Dashboard page.
        r = client.get("/registry", follow_redirects=False)
        assert r.status_code in (302, 307)
        assert r.headers["location"].endswith("/dashboard")

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
