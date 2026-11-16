"""GET /wbs.png and /wbs.html draw the chart from the current PRODUCT.MD instead of serving a stale docs/ file."""
from app import wbs


def test_png_and_html_are_drawn_from_the_current_product(client, tmp_project):
    png = client.get("/wbs.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    html = client.get("/wbs.html")
    assert html.status_code == 200 and "<html" in html.text.lower() and "Stats panel" in html.text

    (tmp_project / "PRODUCT.MD").write_text((tmp_project / "PRODUCT.MD").read_text().replace("Stats panel", "Zebra panel"))
    assert "Zebra panel" in client.get("/wbs.html").text and "Stats panel" not in client.get("/wbs.html").text


def test_same_text_is_generated_once(client, monkeypatch):
    calls = []
    wbs._CACHE.clear()
    real = wbs.regenerate
    monkeypatch.setattr(wbs, "regenerate", lambda d: (calls.append(d), real(d))[1])
    wbs._CACHE.clear()
    client.get("/wbs.png"); client.get("/wbs.html"); client.get("/wbs.png")
    assert len(calls) == 1


def test_docs_snapshot_is_left_alone(client, tmp_project):
    client.get("/wbs.png")
    assert not (tmp_project / "docs" / "wbs.png").exists()
