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
