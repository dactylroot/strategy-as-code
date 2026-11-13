"""Drafting a program initiative end to end: write it to the manifest, send each project its planning task,
the project breaks it out into features, and the program view follows."""
import pytest

import app.auth as auth_mod
from app import scope
from app.registry import Registry, run_git
from tests.test_manifest_repo import git, make_repo
from tests.test_project_scope import make_origin

MANIFEST = """# Program

## Projects

| Key | Repo / Path | Role | Depends On | Mode | Planning Dir |
| --- | ----------- | ---- | ---------- | ---- | ------------ |
| alpha | {alpha} | Core | | direct | . |
| beta | {beta} | Consumer | alpha | direct | plan |
"""


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(auth_mod, "cfg", {"enabled": False, "username": "admin", "password": "changeme", "secret_key": "a" * 64})


@pytest.fixture()
def prog(tmp_path):
    alpha = make_origin(tmp_path, "alpha", "Alpha")
    beta = make_origin(tmp_path, "beta", "Beta", "plan")
    manifest = make_repo(tmp_path, "program", {"PROGRAM.MD": MANIFEST.format(alpha=alpha, beta=beta)})
    reg = Registry.from_manifest_repo(str(manifest), "PROGRAM.MD", cache_dir=tmp_path / "cache")
    scope.set_registry(reg)
    yield reg, {"manifest": manifest, "alpha": alpha, "beta": beta}
    scope.set_registry(None)


H = {"X-Host-User": "planner@example.com"}
DRAFT = {"name": "Search Overhaul", "kind": "major", "goal": "Find things.", "success": "Under 300 ms",
         "asks": [{"project": "beta", "text": "Results page"}, {"project": "alpha", "text": "Index API"}]}


def show(origin, path):
    return run_git(origin, ["show", f"main:{path}"])


def files(origin, prefix):
    return [f for f in run_git(origin, ["ls-tree", "-r", "--name-only", "main"]).splitlines() if f.startswith(prefix)]


def initiative(client, status="draft,planned,in progress,complete", name="Search Overhaul"):
    got = client.get(f"/api/program/initiatives?status={status}").json()["initiatives"]
    return next((i for i in got if i["name"] == name), None)


def test_a_draft_is_written_to_the_manifest_and_listed_with_its_asks(client, prog):
    reg, o = prog
    r = client.post("/api/program/initiatives", json=DRAFT, headers=H)
    assert r.status_code == 201 and r.json()["versioned"], r.text
    text = show(o["manifest"], "PROGRAM.MD")
    assert "### Search Overhaul (Major, Draft)\nGoal: Find things.\nSuccess: Under 300 ms\nAsk beta: Results page\nAsk alpha: Index API" in text
    assert "| alpha |" in text and "| beta |" in text
    assert run_git(o["manifest"], ["log", "-1", "--format=%ae", "main"]) == "planner@example.com"
    d = initiative(client)
    assert (d["status"], d["draft"], d["total"]) == ("Draft", True, 0)
    assert [(a["project"], a["state"], a["waiting_on"]) for a in d["asks"]] == [("alpha", "Drafted", []), ("beta", "Drafted", ["alpha"])]
    assert initiative(client, "planned,in progress") is None          # drafts stay out of the working list


def test_drafts_are_listed_after_the_working_initiatives_with_their_asks(client, prog):
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    page = client.get("/p").text
    mine, working = page.index('ini-name">Search Overhaul'), page.index('ini-name">Reporting Push')
    assert working < mine                                        # drafts sit at the bottom of the list
    row = page[mine:page.index("</details>", mine)]
    assert "draft" in row and "alpha drafted" in row and "beta drafted" in row
    assert "Goal:</b> Find things." in row and "Index API" in row and "waits on alpha" in row
    assert "1 draft below" in page and "0/0" not in row
    assert 'class="send" data-name="Search Overhaul"' in row and "Send to alpha, beta" in row
    assert "send-ordered\" checked" in row                       # beta waits on alpha, so hold it by default
    # Once every ask is sent there is nothing left to send, so the button goes away.
    client.post("/api/program/initiatives/Search Overhaul/send", json={}, headers=H)
    page = client.get("/p").text
    assert "class=\"btn send-go\"" not in page[page.index('ini-name">Search Overhaul'):]


def test_sending_in_dependency_order_then_breaking_out_then_the_dependent_is_released(client, prog):
    reg, o = prog
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    r = client.post("/api/program/initiatives/Search Overhaul/send", json={"ordered": True}, headers=H).json()
    assert r["ok"] and [(x["project"], x["sent"], x["skipped"]) for x in r["results"]] == [
        ("alpha", True, ""), ("beta", False, "waiting for alpha")]
    (item,) = files(o["alpha"], ".planning/inbox/")
    task = show(o["alpha"], item)
    assert "type: initiative" in task and "initiative: Search Overhaul" in task and "initiative_kind: major" in task
    assert "Goal: Find things." in task and "Your part: Index API" in task and "Also asked: beta (Results page)" in task
    assert files(o["beta"], "plan/.planning/inbox/") == []
    # Sending again does not duplicate; the state is derived from the project.
    again = client.post("/api/program/initiatives/Search Overhaul/send", json={}, headers=H).json()["results"]
    assert [(x["project"], x["sent"]) for x in again] == [("alpha", False), ("beta", True)]
    assert "already sent" in again[0]["skipped"] and len(files(o["alpha"], ".planning/inbox/")) == 1
    d = initiative(client)
    assert [(a["project"], a["state"]) for a in d["asks"]] == [("alpha", "Sent"), ("beta", "Sent")] and d["pending"] == ["alpha", "beta"]

    # alpha breaks the task out into features.
    item_id = item.rsplit("/", 1)[1][:-3]
    r = client.post(f"/p/alpha/api/inbox/{item_id}/breakout", headers=H, json={"features": [
        {"prefix": "1.1", "name": "Index API", "value": 8, "notes": "REST"}, {"prefix": "1.2", "name": "Query planner"}]})
    assert r.status_code == 200, r.text
    assert r.json()["became"].startswith("2 features: ")
    product, about = show(o["alpha"], "PRODUCT.MD"), show(o["alpha"], "ABOUT.MD")
    assert "Index API" in product and "Query planner" in product and f"(inbox {item_id})" in product
    assert "### Search Overhaul (Major)" in about
    assert files(o["alpha"], ".planning/inbox/") == [] and "outcome: promoted" in show(o["alpha"], f".planning/triaged/{item_id}.md")
    d = initiative(client)
    assert d["status"] == "Planned" and d["total"] == 2 and d["draft"]    # still tagged Draft in the manifest, but it has work now
    assert {a["project"]: a["state"] for a in d["asks"]} == {"alpha": "Defined", "beta": "Sent"} and d["pending"] == ["beta"]
    assert d["asks"][0]["detail"] == "2 features"


def test_a_declined_ask_can_be_sent_again(client, prog):
    reg, o = prog
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    client.post("/api/program/initiatives/Search Overhaul/send", json={"projects": ["beta"]}, headers=H)
    (item,) = files(o["beta"], "plan/.planning/inbox/")
    item_id = item.rsplit("/", 1)[1][:-3]
    assert client.post(f"/p/beta/api/inbox/{item_id}/decline", json={"reason": "not this quarter"}, headers=H).status_code == 200
    beta = {a["project"]: a for a in initiative(client)["asks"]}["beta"]
    assert (beta["state"], beta["detail"]) == ("Declined", "not this quarter")
    again = client.post("/api/program/initiatives/Search Overhaul/send", json={"projects": ["beta"]}, headers=H).json()["results"]
    assert again == [{"project": "beta", "sent": True, "skipped": "", "error": ""}]
    assert {a["project"]: a["state"] for a in initiative(client)["asks"]}["beta"] == "Sent"


def test_publish_change_and_remove(client, prog):
    reg, o = prog
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    r = client.patch("/api/program/initiatives/Search Overhaul", json={"draft": False, "goal": "Better.", "asks": []}, headers=H)
    assert r.status_code == 200
    text = show(o["manifest"], "PROGRAM.MD")
    assert "### Search Overhaul (Major)\nGoal: Better.\n" in text and "Ask " not in text and "Success: Under 300 ms" in text
    assert client.delete("/api/program/initiatives/Search Overhaul", headers=H).status_code == 200
    assert "Search Overhaul" not in show(o["manifest"], "PROGRAM.MD") and "| beta |" in show(o["manifest"], "PROGRAM.MD")
    assert client.delete("/api/program/initiatives/Search Overhaul", headers=H).status_code == 422


def test_bad_requests_are_refused_and_change_nothing(client, prog):
    reg, o = prog
    before = run_git(o["manifest"], ["rev-parse", "main"])
    assert client.post("/api/program/initiatives", json={**DRAFT, "asks": [{"project": "nope"}]}).status_code == 422
    assert client.post("/api/program/initiatives", json={**DRAFT, "name": "X (Major)"}).status_code == 422
    assert client.post("/api/program/initiatives", json={**DRAFT, "kind": "huge"}).status_code == 422
    assert run_git(o["manifest"], ["rev-parse", "main"]) == before
    assert client.post("/api/program/initiatives", json=DRAFT).status_code == 201
    assert client.post("/api/program/initiatives", json={**DRAFT, "name": "search overhaul"}).status_code == 409
    assert client.post("/api/program/initiatives/Nope/send", json={}).status_code == 404
    assert client.post("/api/program/initiatives/Search Overhaul/send", json={"projects": ["nope"]}).status_code == 422
    assert client.patch("/p/alpha/api/program/initiatives/Search Overhaul", json={}).status_code == 404   # program-level only
    assert client.get("/p/alpha/api/program/initiatives").status_code == 404


def test_a_draft_with_no_asks_cannot_be_sent(client, prog):
    client.post("/api/program/initiatives", json={"name": "Empty"})
    r = client.post("/api/program/initiatives/Empty/send", json={})
    assert r.status_code == 422 and "no asks" in r.json()["detail"]


def test_planning_tasks_are_only_resolved_by_breaking_out_and_cannot_be_submitted(client, prog):
    reg, o = prog
    client.get("/p/alpha/features")
    r = client.post("/p/alpha/api/inbox", json={"type": "initiative", "title": "Plan: X"}, headers=H)
    assert r.status_code == 422
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    client.post("/api/program/initiatives/Search Overhaul/send", json={"projects": ["alpha"]}, headers=H)
    item_id = files(o["alpha"], ".planning/inbox/")[0].rsplit("/", 1)[1][:-3]
    assert client.post(f"/p/alpha/api/inbox/{item_id}/promote", json={"prefix": "1.1"}, headers=H).status_code == 422
    assert client.post(f"/p/alpha/api/inbox/{item_id}/breakout", json={"features": []}, headers=H).status_code == 422
    assert client.post(f"/p/alpha/api/inbox/{item_id}/breakout", headers=H,
                       json={"features": [{"prefix": "9.9", "name": "x"}]}).status_code == 422
    assert files(o["alpha"], ".planning/inbox/") != []                 # still waiting; nothing was half-applied
    assert "Search Overhaul" not in show(o["alpha"], "ABOUT.MD") and "- x" not in show(o["alpha"], "PRODUCT.MD")
    feat = client.post("/p/alpha/api/inbox", json={"type": "feature", "title": "Dark mode"}, headers=H).json()["id"]
    assert client.post(f"/p/alpha/api/inbox/{feat}/breakout", headers=H,
                       json={"features": [{"prefix": "1.1", "name": "x"}]}).status_code == 422


def test_only_hub_admins_may_change_or_send_program_initiatives(client, prog):
    from types import SimpleNamespace as NS
    from fastapi import HTTPException
    from app import hub_identity as hub
    from app.routers import api
    for role, allowed in (("viewer", False), ("editor", False), ("admin", True)):
        req = NS(state=NS(identity=hub.Identity(email="x@example.com", role=role)))
        if allowed:
            assert api._program_registry(req) is not None
        else:
            with pytest.raises(HTTPException) as e:
                api._program_registry(req)
            assert e.value.status_code == 403 and "hub admins" in e.value.detail


def test_the_overview_offers_a_new_initiative_form_with_a_checkbox_per_project(client, prog):
    page = client.get("/p").text
    form = page[page.index('<form id="newini"'):page.index("</form>", page.index('<form id="newini"'))]
    assert 'name="name"' in form and 'name="kind"' in form and 'name="goal"' in form and 'name="success"' in form
    for key in ("alpha", "beta"):
        assert f'name="ask" value="{key}"' in form and f'name="hint-{key}"' in form
    assert 'name="draft" checked' in form
    assert "define its requirements, once you Create it." in form       # matches the button
    # The form posts to the same endpoint the API tests exercise.
    assert "/api/program/initiatives'" in page



def test_a_declared_initiative_has_an_edit_form_prefilled_with_a_delete_button(client, prog):
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    page = client.get("/p").text
    row = page[page.index('ini-name">Search Overhaul'):]
    form = row[row.index('data-name="Search Overhaul"'):row.index("</form>")]
    assert "Edit or delete this initiative" in row and "ini-del" in form and "Save changes" in form
    assert "Find things." in form and "Under 300 ms" in form                 # goal and success are prefilled
    assert 'value="alpha" checked' in form and 'value="Index API"' in form    # the ask and its hint are prefilled
    assert 'value="beta" checked' in form and 'name="draft" checked' in form
    assert 'option value="major" selected' in form
    # An initiative that only exists in project roadmaps has nothing in the manifest to edit.
    assert page.count('data-name="Reporting Push"') == 0


def test_saving_the_edit_form_changes_and_clears_fields_and_delete_removes_it(client, prog):
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    # What the form sends: every field, with blanks meaning "remove this line".
    body = {"kind": "minor", "goal": "", "success": "Under 200 ms", "draft": True,
            "asks": [{"project": "beta", "text": ""}]}
    assert client.patch("/api/program/initiatives/Search Overhaul", json=body, headers=H).status_code == 200
    d = initiative(client)
    assert d["kind"] == "minor" and d["goal"] == "" and d["success"] == "Under 200 ms"
    assert [(a["project"], a["text"]) for a in d["asks"]] == [("beta", "")]
    assert client.delete("/api/program/initiatives/Search Overhaul", headers=H).status_code == 200
    assert "Search Overhaul" not in client.get("/p").text


def test_program_initiatives_are_the_last_section_of_the_overview_page(client, prog):
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    page = client.get("/p").text
    assert page.index('class="tablewrap"') < page.index("Program initiatives</h2>") < page.index('ini-name">Search Overhaul')
    assert page.index("</table>") < page.index("Program initiatives</h2>")
    assert page.index("Program initiatives</h2>") < page.index('id="newini"')


def test_the_inbox_breaks_a_planning_task_out_into_a_feature_list_instead_of_promoting_it(client, prog):
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    client.post("/api/program/initiatives/Search Overhaul/send", json={}, headers=H)
    client.post("/p/alpha/api/inbox", json={"type": "feature", "title": "Dark mode", "body": ""}, headers={"X-Host-User": "other@example.com"})
    page = client.get("/p/alpha/inbox").text
    task = page[page.index('class="breakout"'):]
    task = task[:task.index('data-id=', 1)] if 'data-id=' in task[1:] else task
    assert 'data-initiative="Search Overhaul"' in page and "planning task" in page
    assert "Break out into features" in page and "bo-rows" in page
    assert "Your part: Index API" in page                       # the task's own text is shown above the editor
    # Only the plain feature request keeps Promote and Duplicate; the planning task offers Decline with a reason.
    assert page.count('class="btn btn-primary promote"') == 1 and page.count('class="btn dup"') == 1
    assert "Why no work is needed here" in page
    assert page.index("const SUB_AREAS") < page.index("document.querySelectorAll('.inbox-item')")
    # The editor lists this project's sub-areas for the features.
    import json
    line = next(l for l in page.splitlines() if l.startswith("const SUB_AREAS"))
    areas = json.loads(line[line.index("["):line.rindex("]") + 1])
    assert areas and all(a["wbs"] and a["name"] for a in areas)    # real sub-areas, not blanks
    first = areas[0]
    assert f'<option value="{first["wbs"]}">{first["wbs"]} {first["name"]}</option>' in page    # the Promote dropdown too



def test_initiative_links_open_the_projects_own_planning_pages(client, prog):
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    client.post("/api/program/initiatives/Search Overhaul/send", json={"projects": ["alpha"]}, headers=H)
    page = client.get("/p").text
    row = page[page.index('ini-name">Search Overhaul'):]
    row = row[:row.index("</form>")]
    assert 'href="/p/alpha/inbox"' in row           # sent: the task is waiting in alpha's inbox
    assert 'href="/p/beta/dashboard"' in row        # drafted: nothing in beta yet, so its portal
    assert 'href="#alpha"' not in row and 'href="#beta"' not in row
    working = page[page.index('ini-name">Reporting Push'):page.index('ini-name">Search Overhaul')]
    body = working.split("</summary>", 1)[1]
    assert "/roadmap?initiative=Reporting%20Push" in body and "/features?q=" in body and 'href="#' not in body


def _task_for_alpha(client, o):
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    client.post("/api/program/initiatives/Search Overhaul/send", json={"projects": ["alpha"]}, headers=H)
    (item,) = files(o["alpha"], ".planning/inbox/")
    return item.rsplit("/", 1)[1][:-3]


def test_a_task_can_link_features_the_project_already_has_alongside_new_ones(client, prog):
    reg, o = prog
    item_id = _task_for_alpha(client, o)
    page = client.get("/p/alpha/inbox").text
    assert "const FEATURES = [" in page and "AlphaLogin" in page                 # what the editor can search and suggest
    assert f'"{item_id}"' in page and '"hint": "Index API"' in page              # the ask's hint seeds the suggestions
    assert "bo-search" in page and "Features this project already has" in page

    bad = client.post(f"/p/alpha/api/inbox/{item_id}/breakout", headers=H, json={"existing": ["9.9.9"], "features": []})
    assert bad.status_code == 422 and "no such feature to link: 9.9.9" in bad.json()["detail"]
    assert client.post(f"/p/alpha/api/inbox/{item_id}/breakout", headers=H, json={}).status_code == 422   # nothing to define

    r = client.post(f"/p/alpha/api/inbox/{item_id}/breakout", headers=H, json={
        "features": [{"prefix": "1.1", "name": "Index API", "value": 8}], "existing": ["1.1.1", "1.1.1"]})
    assert r.status_code == 200, r.text
    assert r.json()["became"].startswith("1 feature: ") and "; linked 1 existing: 1.1.1" in r.json()["became"]
    about = show(o["alpha"], "ABOUT.MD")
    new_code = r.json()["became"].split("feature: ")[1].split(";")[0]
    block = about[about.index("### Search Overhaul (Major)"):]
    assert f"- {new_code}" in block and "- 1.1.1" in block
    assert files(o["alpha"], ".planning/inbox/") == []


def test_a_task_can_be_defined_entirely_from_existing_features(client, prog):
    reg, o = prog
    item_id = _task_for_alpha(client, o)
    before = show(o["alpha"], "PRODUCT.MD")
    r = client.post(f"/p/alpha/api/inbox/{item_id}/breakout", headers=H, json={"existing": ["1.1.1"]})
    assert r.status_code == 200 and r.json()["became"] == "linked 1 existing: 1.1.1"
    assert show(o["alpha"], "PRODUCT.MD") == before                    # nothing new was added
    assert "- 1.1.1" in show(o["alpha"], "ABOUT.MD")
    d = initiative(client)
    assert next(a for a in d["asks"] if a["project"] == "alpha")["state"] == "Defined"



def test_a_new_name_is_matched_against_program_initiatives_aliases_and_project_roadmaps(client, prog):
    client.post("/api/program/initiatives", json=DRAFT, headers=H)
    got = client.get("/api/program/initiatives/match?name=  search   OVERHAUL. ").json()["match"]
    assert (got["name"], got["declared"], got["alias"]) == ("Search Overhaul", True, False)
    got = client.get("/api/program/initiatives/match?name=Reporting push").json()["match"]
    assert got["name"] == "Reporting Push" and got["projects"]        # a project already plans under that name
    assert client.get("/api/program/initiatives/match?name=Brand new thing").json()["match"] is None
    assert client.get("/api/program/initiatives/match?name=").json()["match"] is None


def test_the_new_form_warns_about_a_name_that_is_already_in_use(client, prog):
    page = client.get("/p").text
    assert 'class="ini-dup' in page and "/match?name=" in page


def test_the_features_and_roadmap_pages_open_at_a_linked_search_or_initiative(client, prog):
    assert "get('q')" in client.get("/p/alpha/features").text
    assert "get('initiative')" in client.get("/p/alpha/roadmap").text


def test_the_break_out_editor_constants_are_declared_before_the_rows_are_wired(client, prog):
    """`const` is not hoisted: the editor runs while the rows are wired up, so what it reads must come first."""
    page = client.get("/p/alpha/inbox").text
    wired = page.index("querySelectorAll('.inbox-item').forEach")
    for name in ("const STOP", "const FEATURES", "const TASKS", "const SUB_AREAS"):
        assert page.index(name) < wired, name


def test_the_product_scope_modal_does_not_repeat_the_area_name_and_does_not_call_scope_items_initiatives(client, prog):
    page = client.get("/p/alpha/structure").text
    assert "Product scope area" in page and "Area name" in page and "Scope items" in page
    assert "Group Title" not in page and "Add initiative" not in page and 'placeholder="Initiative' not in page
    assert '<textarea class="scope-text"' in page      # items are sentences: shown in full, not cut off in a one-line box
