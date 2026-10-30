"""The contributor role: a Power User of a project's app, seen from the Project Planning side."""
from datetime import datetime, timedelta, timezone

import pytest

from app import inbox
from app.registry import run_git
from app.routers import api as api_module
from tests.test_hub_identity import as_user, hub_mode  # noqa: F401
from tests.test_project_scope import origin_of, program, remote_text  # noqa: F401

ADA = "ada@example.com"
BOB = "bob@example.com"


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    api_module._submit_log.clear()


def submit(client, email, kind="bug", title="Export fails", **extra):
    as_user(client, "contributor", email)
    r = client.post("/p/alpha/api/inbox", json={"type": kind, "title": title, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def triage(client, item_id, action, body):
    as_user(client, "editor", "grace@example.com")
    r = client.post(f"/p/alpha/api/inbox/{item_id}/{action}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_hub_is_asked_about_the_project_the_request_is_for(client, program, hub_mode):  # noqa: F811
    as_user(client, "contributor", ADA)
    client.get("/p/alpha/features")
    client.get("/p/beta/features")
    urls = [u for u, _ in hub_mode]
    assert urls[0].endswith("&project=alpha") and urls[1].endswith("&project=beta")


def test_a_contributor_opens_features_and_bugs_and_nothing_else(client, program, hub_mode):  # noqa: F811
    as_user(client, "contributor", ADA)
    assert client.get("/p/alpha/features").status_code == 200
    assert client.get("/p/alpha/bugs").status_code == 200
    for page in ("dashboard", "roadmap", "structure", "inbox", "about", "readme"):
        assert client.get(f"/p/alpha/{page}").status_code == 403, page
    for url in ("/p", "/work", "/dashboard", "/features", "/p/alpha/api/product", "/p/alpha/api/bugs",
                "/p/alpha/api/inbox", "/p/alpha/api/sync-status"):
        assert client.get(url).status_code == 403, url
    assert client.patch("/p/alpha/api/features/1.1.2", json={"notes": "x"}).status_code == 403
    assert client.post("/p/alpha/api/bugs", json={"title": "x"}).status_code == 403
    assert client.patch("/p/alpha/api/bugs/1", json={"notes": "x"}).status_code == 403
    assert client.post("/p/alpha/api/inbox/any-id/promote", json={}).status_code == 403
    assert client.post("/p/alpha/api/inbox/any-id/decline", json={}).status_code == 403


def test_the_project_root_leads_a_contributor_to_features(client, program, hub_mode):  # noqa: F811
    as_user(client, "contributor", ADA)
    r = client.get("/p/alpha", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/p/alpha/features"


def test_the_contributor_nav_lists_only_features_and_bugs(client, program, hub_mode):  # noqa: F811
    as_user(client, "contributor", ADA)
    page = client.get("/p/alpha/features").text
    assert 'href="/p/alpha/bugs"' in page
    for gone in ("/p/alpha/dashboard", "/p/alpha/roadmap", "/p/alpha/structure", "/p/alpha/inbox", 'href="/p"'):
        assert gone not in page, gone


def test_a_contributor_sees_every_feature_with_its_notes_and_no_edit_controls(client, program, hub_mode):  # noqa: F811
    as_user(client, "editor", "grace@example.com")
    assert client.patch("/p/alpha/api/features/1.1.2", json={"notes": "Internal plumbing note"}).status_code == 200
    as_user(client, "contributor", ADA)
    page = client.get("/p/alpha/features").text
    assert "AlphaLogin" in page and "Internal plumbing note" in page
    assert "/api/features" not in page


def test_a_submission_is_a_file_by_the_contributor_and_committed_as_them(client, program, hub_mode):  # noqa: F811
    item_id = submit(client, ADA, "feature", "Dark mode")
    text = remote_text(program, "alpha", f".planning/inbox/{item_id}.md")
    assert "submitter: ada@example.com" in text and "type: feature" in text
    assert run_git(origin_of(program, "alpha"), ["log", "-1", "--format=%ae", "main"]) == ADA


def test_a_contributor_edits_and_withdraws_only_their_own_waiting_items(client, program, hub_mode):  # noqa: F811
    mine = submit(client, ADA, "bug", "Mine", body="first")
    theirs = submit(client, BOB, "bug", "Theirs")
    as_user(client, "contributor", ADA)
    r = client.patch(f"/p/alpha/api/inbox/{mine}", json={"title": "Mine, clearer", "body": "second"})
    assert r.status_code == 200 and r.json()["title"] == "Mine, clearer"
    text = remote_text(program, "alpha", f".planning/inbox/{mine}.md")
    assert "title: Mine, clearer" in text and "second" in text and "submitter: ada@example.com" in text
    assert client.patch(f"/p/alpha/api/inbox/{theirs}", json={"title": "Hijacked"}).status_code == 403
    assert client.delete(f"/p/alpha/api/inbox/{theirs}").status_code == 403
    assert "title: Theirs" in remote_text(program, "alpha", f".planning/inbox/{theirs}.md")
    assert client.delete(f"/p/alpha/api/inbox/{mine}").status_code == 200
    assert client.patch(f"/p/alpha/api/inbox/{mine}", json={"title": "again"}).status_code == 422


def test_an_item_cannot_be_edited_once_it_is_triaged(client, program, hub_mode):  # noqa: F811
    item_id = submit(client, ADA, "bug", "Crash")
    triage(client, item_id, "decline", {"reason": "works as designed"})
    as_user(client, "contributor", ADA)
    assert client.patch(f"/p/alpha/api/inbox/{item_id}", json={"title": "x"}).status_code == 422
    assert client.delete(f"/p/alpha/api/inbox/{item_id}").status_code == 422


def test_screenshots_of_another_persons_item_are_refused(client, program, hub_mode):  # noqa: F811
    theirs = submit(client, BOB, "bug", "Theirs")
    as_user(client, "contributor", ADA)
    assert client.get(f"/p/alpha/api/inbox/{theirs}/screenshot").status_code == 403
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    assert client.post(f"/p/alpha/api/inbox/{theirs}/screenshot", files={"file": ("a.png", png, "image/png")}).status_code == 403


def test_outcomes_stay_with_the_submitter_after_triage(client, program, hub_mode):  # noqa: F811
    bug = submit(client, ADA, "bug", "Export fails", wbs="1.1.1", body="private repro steps")
    declined = submit(client, ADA, "feature", "Teleport")
    dup = submit(client, ADA, "feature", "Login again")
    other = submit(client, BOB, "bug", "Not Ada's")
    became = triage(client, bug, "promote", {})["became"]
    triage(client, declined, "decline", {"reason": "out of scope"})
    triage(client, dup, "decline", {"duplicate_of": "1.1.2"})
    record = remote_text(program, "alpha", f".planning/triaged/{bug}.md")
    assert "outcome: promoted" in record and "private repro steps" not in record     # the description is not kept
    as_user(client, "contributor", ADA)
    rows = {r["title"]: r for r in client.get("/p/alpha/api/inbox/mine").json()}
    assert set(rows) == {"Export fails", "Teleport", "Login again"}                                    # never Bob's
    assert (rows["Export fails"]["outcome"], rows["Export fails"]["ref"], rows["Export fails"]["state"]) == ("promoted", became, "Open")
    assert (rows["Teleport"]["outcome"], rows["Teleport"]["reason"]) == ("declined", "out of scope")
    assert (rows["Login again"]["outcome"], rows["Login again"]["ref"]) == ("duplicate", "1.1.2")
    as_user(client, "contributor", BOB)
    assert [(r["id"], r["outcome"]) for r in client.get("/p/alpha/api/inbox/mine").json()] == [(other, "waiting")]


def test_a_promoted_bug_shows_its_current_status_to_its_submitter(client, program, hub_mode):  # noqa: F811
    bug = submit(client, ADA, "bug", "Export fails", wbs="1.1.1")
    became = triage(client, bug, "promote", {})["became"]
    bug_id = int(became.split()[1])
    as_user(client, "editor", "grace@example.com")
    assert client.patch(f"/p/alpha/api/bugs/{bug_id}", json={"status": "Resolved", "fix_version": "0.2.1"}).status_code == 200
    as_user(client, "contributor", ADA)
    assert client.get("/p/alpha/api/inbox/mine").json()[0]["state"] == "Resolved"


def test_contributor_bugs_hide_other_peoples_notes_and_bugs_without_a_wbs(client, program, hub_mode):  # noqa: F811
    as_user(client, "editor", "grace@example.com")
    client.get("/p/alpha/bugs")
    assert client.post("/p/alpha/api/bugs", json={"title": "Visible title", "notes": "SECRET internals", "wbs_ref": "1.1.1"}).status_code == 200
    assert client.post("/p/alpha/api/bugs", json={"title": "Unmapped bug", "notes": "ALSO SECRET"}).status_code == 200
    mine = submit(client, ADA, "bug", "Ada's own", body="my repro steps", wbs="1.1.1")
    triage(client, mine, "promote", {})
    as_user(client, "contributor", ADA)
    page = client.get("/p/alpha/bugs").text
    assert "Visible title" in page and "SECRET internals" not in page
    assert "Unmapped bug" not in page and "ALSO SECRET" not in page
    assert "Ada&#39;s own" in page and "my repro steps" in page
    as_user(client, "contributor", BOB)
    page = client.get("/p/alpha/bugs").text
    assert "Visible title" in page and "my repro steps" not in page


def test_the_editor_view_is_unchanged_for_staff(client, program, hub_mode):  # noqa: F811
    as_user(client, "editor", "grace@example.com")
    page = client.get("/p/alpha/bugs").text
    assert "newBugModal" in page and "Your submissions" not in page
    assert client.get("/p/alpha/dashboard").status_code == 200


def test_triaged_records_are_pruned_after_a_year_and_not_before(tmp_path):
    item = inbox.write_item(tmp_path, "bug", "Old", "d", "ada@example.com")
    inbox.decline(tmp_path, item.id, reason="no")
    record = tmp_path / inbox.TRIAGED / f"{item.id}.md"
    soon = datetime.now(timezone.utc) + timedelta(days=inbox.TRIAGED_DAYS - 2)
    inbox.sweep(tmp_path, now=soon)
    assert record.exists()
    inbox.sweep(tmp_path, now=datetime.now(timezone.utc) + timedelta(days=inbox.TRIAGED_DAYS + 2))
    assert not record.exists()


def test_submitter_matching_ignores_case_and_a_missing_submitter_matches_nobody(tmp_path):
    item = inbox.write_item(tmp_path, "bug", "T", "", "Ada@Example.com")
    assert inbox.update_item(tmp_path, item.id, "ada@example.com", title="U").title == "U"
    anon = inbox.write_item(tmp_path, "bug", "Anon", "", "")
    with pytest.raises(inbox.NotSubmitter):
        inbox.delete_item(tmp_path, anon.id, "")


def test_an_item_id_ends_with_a_tag_of_its_submitters_email(tmp_path):
    item = inbox.write_item(tmp_path, "bug", "T", "", "Ada@Example.com")
    assert item.id.endswith("-" + inbox.submitter_tag("ada@example.com")) and len(inbox.submitter_tag("x@y")) == 9
    assert inbox.submitter_tag(" ADA@example.com ") == inbox.submitter_tag("ada@example.com")
    assert inbox.submitter_tag("bob@example.com") != inbox.submitter_tag("ada@example.com")
    assert not inbox.write_item(tmp_path, "bug", "T", "", "cli-user").id.endswith(inbox.submitter_tag("cli-user"))
