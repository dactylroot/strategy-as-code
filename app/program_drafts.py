"""Send a draft initiative's planning tasks to the projects it asks.

Each `Ask <project>:` line of a draft becomes one inbox item of kind `initiative` in that project: a task for
the project's owners to break the initiative out into features and define its requirements there (see
`inbox.breakout`). The item is a snapshot of the draft's goal and success criteria plus what the other
projects were asked, so the owner sees the whole picture. Which asks are Drafted, Sent, Breaking out,
Defined or Declined is never stored: `overview.ask_state` reads it from the projects.

One commit per project, through the same writer as every other planning write. A project that fails is
reported and the others still go through.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor

from . import inbox, overview
from .manifest import normalize_name
from .registry import Registry
from .writer import Actor, edit

SENDABLE = ("Drafted", "Declined")      # a declined ask can be reworded and sent again


class DraftError(ValueError):
    pass


def find_draft(views: list[overview.ProgramInitiativeView], name: str) -> overview.ProgramInitiativeView:
    want = normalize_name(name)
    for v in views:
        if v.declared and not v.separate_of and normalize_name(v.name) == want:
            return v
    raise DraftError(f"no initiative named {name!r} in the program manifest")


def task_body(view: overview.ProgramInitiativeView, project: str, depends_on: tuple[str, ...]) -> str:
    mine = next(a for a in view.asks if a["project"] == project)
    lines = [f"Define the requirements for the program initiative \"{view.name}\" ({view.kind}) in this project."]
    if view.goal:
        lines += ["", f"Goal: {view.goal}"]
    if view.success:
        lines += [f"Success: {view.success}"]
    if mine["text"]:
        lines += ["", f"Your part: {mine['text']}"]
    others = [a for a in view.asks if a["project"] != project]
    if others:
        lines += ["", "Also asked: " + "; ".join(f"{a['project']}" + (f" ({a['text']})" if a["text"] else "") for a in others)]
    asked = {a["project"]: a["state"] for a in view.asks}
    needs = [f"{d} ({asked[d].lower()})" for d in depends_on if d in asked]
    if needs:
        lines += ["Depends on: " + ", ".join(needs)]
    lines += ["", f"Break this out into features in this project's WBS; they are listed under the initiative "
                  f"\"{view.name}\" in your roadmap. Decline with a reason if no work is needed here."]
    return "\n".join(lines)


def send(registry: Registry, name: str, projects: list[str] | None = None, ordered: bool = False,
         actor: Actor | None = None) -> list[dict]:
    """Send the planning task of each ask that is Drafted or Declined (all of them, or only `projects`).
    With `ordered`, an ask waits while a project it depends on, also asked, has not defined its part.
    One result per ask: {project, sent, skipped, error}. Blocking."""
    rows = overview.summarize_all()
    views, error = overview.unified_initiatives(rows)
    if error:
        raise DraftError(f"program initiatives could not be read: {error}")
    view = find_draft(views, name)
    if not view.asks:
        raise DraftError(f"{view.name!r} has no asks to send; add an 'Ask <project>:' line for each project")
    unknown = sorted(set(projects or ()) - {a["project"] for a in view.asks})
    if unknown:
        raise DraftError(f"{view.name!r} does not ask {', '.join(unknown)}")
    todo, results = [], []
    for a in view.asks:
        if projects is not None and a["project"] not in projects:
            continue
        if a["state"] not in SENDABLE:
            results.append({"project": a["project"], "sent": False, "skipped": f"already {a['state'].lower()}", "error": ""})
        elif ordered and a["waiting_on"]:
            results.append({"project": a["project"], "sent": False, "skipped": "waiting for " + ", ".join(a["waiting_on"]), "error": ""})
        else:
            todo.append(a)

    def one(a: dict) -> dict:
        key = a["project"]
        spec = registry.get(key).spec
        body = task_body(view, key, spec.depends_on)

        def apply(checkout) -> None:
            root = checkout / spec.planning_dir
            # A retry on a fresh checkout, or a second sender, must not leave two open tasks.
            if any(i.kind == inbox.TASK and normalize_name(i.initiative) == normalize_name(view.name)
                   for i in inbox.list_items(root)):
                return
            inbox.write_item(root, inbox.TASK, f"Plan: {view.name}", body, actor.name if actor else "program",
                             initiative=view.name, initiative_kind=view.kind)

        try:
            edit(registry, key, apply, f"UI: initiative {view.name} sent to {key}", actor)
        except Exception as exc:    # reported per project; never stops the others
            return {"project": key, "sent": False, "skipped": "", "error": str(exc) or exc.__class__.__name__}
        return {"project": key, "sent": True, "skipped": "", "error": ""}

    if todo:
        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
            results += list(pool.map(one, todo))
    order = {a["project"]: n for n, a in enumerate(view.asks)}
    return sorted(results, key=lambda r: order[r["project"]])
