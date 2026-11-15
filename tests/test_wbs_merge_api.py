"""POST /api/structure/wbs/preview and /move (disk mode): merge a sub-area into another, even in another area."""


def test_preview_writes_nothing(client, tmp_project):
    before = (tmp_project / "PRODUCT.MD").read_text()
    r = client.post("/api/structure/wbs/preview", json={"source": "1.2", "target": "1.1"})
    assert r.status_code == 200
    body = r.json()
    assert body["mapping"] == {"1.2.1": "1.1.4", "1.2.2": "1.1.5", "1.2.3": "1.1.6"}
    assert body["initiatives_updated"] == 1 and body["removed_sub_area"] == "1.2"
    assert (tmp_project / "PRODUCT.MD").read_text() == before


def test_merge_moves_features_follows_initiative_and_removes_heading(client, tmp_project):
    r = client.post("/api/structure/wbs/move", json={"source": "1.2", "target": "1.1"})
    assert r.status_code == 200 and r.json()["mapping"]["1.2.2"] == "1.1.5"
    product = (tmp_project / "PRODUCT.MD").read_text()
    assert "#### 1.2 " not in product and "| 1.1.5 | Stats panel |" in product
    assert "- 1.1.5" in (tmp_project / "ABOUT.MD").read_text()
    assert not (tmp_project / "BUGS.MD").exists()
    assert client.get("/api/product").json()["wbs_areas"][0]["sub_areas"][0]["wbs_prefix"] == "1.1"


def test_move_chosen_features_only(client, tmp_project):
    r = client.post("/api/structure/wbs/move", json={"wbs": ["1.2.3"], "target": "1.1"})
    assert r.status_code == 200 and r.json()["mapping"] == {"1.2.3": "1.1.4"}
    assert "#### 1.2 " in (tmp_project / "PRODUCT.MD").read_text()


def test_bad_requests_are_400_and_leave_files_alone(client, tmp_project):
    before = (tmp_project / "PRODUCT.MD").read_text()
    for body in ({"source": "1.2", "target": "1.2"}, {"source": "9.9", "target": "1.1"}, {"target": "1.1"},
                 {"wbs": ["1.1.1"], "target": "1.1"}):
        assert client.post("/api/structure/wbs/move", json=body).status_code == 400
    assert (tmp_project / "PRODUCT.MD").read_text() == before


def test_structure_page_offers_merge_and_move(client):
    page = client.get("/structure").text
    assert "openWbsMove" in page and "wbs-move-modal" in page and "/api/structure/wbs" in page
    assert '"features": [{"wbs": "1.1.1"' in page


def test_structure_page_script_parses(client, tmp_path):
    """The Structure page's own script (the one holding renderWbs), not the base template's, must be valid JS."""
    import re, shutil, subprocess
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node is not installed")
    scripts = [s for s in re.findall(r"<script>(.*?)</script>", client.get("/structure").text, re.S) if "function renderWbs" in s]
    assert len(scripts) == 1
    f = tmp_path / "structure.js"
    f.write_text(scripts[0], encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_move_a_sub_area_under_another_area(client, tmp_project):
    from app.parsers import product as P
    r = client.post("/api/structure/wbs/preview", json={"source": "1.2", "to_area": 2})
    assert r.status_code == 400 and "Area 2 not found" in r.json()["detail"]      # the fixture has one area
    client.put("/api/structure/wbs", json={"areas": [
        {"number": 1, "title": "Core", "sub_areas": [{"prefix": "1.1", "title": "Auth"}, {"prefix": "1.2", "title": "Dashboard"}]},
        {"number": None, "title": "Ops", "sub_areas": []}]})
    prev = client.post("/api/structure/wbs/preview", json={"source": "1.2", "to_area": 2}).json()
    assert prev["new_sub_area"] == "2.1" and prev["mapping"]["1.2.2"] == "2.1.2" and prev["initiatives_updated"] == 1
    r = client.post("/api/structure/wbs/move", json={"source": "1.2", "to_area": 2})
    assert r.status_code == 200 and r.json()["new_sub_area"] == "2.1"
    doc = P._parse_text((tmp_project / "PRODUCT.MD").read_text())
    assert [(s.wbs_prefix, s.title, len(s.features)) for a in doc.wbs_areas for s in a.sub_areas] == [("1.1", "Auth", 3), ("2.1", "Dashboard", 3)]
    assert "- 2.1.2" in (tmp_project / "ABOUT.MD").read_text()
