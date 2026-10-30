"""`strategy` - change planning files in a member project through the shared writer.

    strategy feature status 3.1.1 live
    strategy feature notes  3.1.1 "Does X now"
    strategy feature score  3.1.1 8 [3]
    strategy feature add    3.1 "Feature name" [--status idea] [--value 8] [--effort 3] [--notes ...]
    strategy feature move   3.1.1 4.2
    strategy bug add "Login times out" --severity high
    strategy bug status 12 resolved --fix 0.4.2
    strategy bug close  12 --resolved-in 0.4.2
    strategy deps funnel [--up] [--depth 2]
    strategy pr -p funnel            (open or refresh the pull request for a branch-mode project)

Every command is one commit pushed to the project's planning target (see
`app.writer.edit`); nothing is edited in the caller's working tree, so it is
safe to run from a feature branch. The program manifest comes from --manifest,
$STRATEGY_MANIFEST, or the nearest PROGRAM.MD at or below a parent of the
current directory. Pass --chart to regenerate docs/wbs.* in the same commit;
it is off by default.
"""
from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from . import inbox as _inbox
from . import pullrequest
from .manifest import MAX_DEPTH, ManifestError, walk
from .models import BugCreate, BugSeverity, BugStatus, BugUpdate, FeatureStatus, NewFeature
from .parsers import about as about_parser
from .parsers import bugs as bugs_parser
from .parsers import product as product_parser
from .registry import Registry, RegistryError
from .writer import Actor, edit

_FEATURE_STATUSES = {"idea": FeatureStatus.idea, "in-progress": FeatureStatus.in_progress,
                     "live": FeatureStatus.live, "released": FeatureStatus.released}
_BUG_STATUSES = {"open": BugStatus.open, "investigating": BugStatus.investigating,
                 "resolved": BugStatus.resolved}
_SEVERITIES = {s.value.lower(): s for s in BugSeverity}


class CliError(Exception):
    pass


def find_manifest(explicit: str | None, start: Path | None = None) -> Path:
    candidate = explicit or os.environ.get("STRATEGY_MANIFEST")
    if candidate:
        p = Path(candidate).expanduser()
        if p.is_dir():
            p = p / "PROGRAM.MD"
        if not p.is_file():
            raise CliError(f"manifest not found: {p}")
        return p
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        for rel in ("PROGRAM.MD", "program/PROGRAM.MD"):
            if (d / rel).is_file():
                return d / rel
    raise CliError("no PROGRAM.MD found; pass --manifest or set STRATEGY_MANIFEST")


def _git_config(key: str) -> str:
    out = subprocess.run(["git", "config", key], capture_output=True, text=True)
    return out.stdout.strip()


def resolve_actor() -> Actor:
    raw = os.environ.get("STRATEGY_ACTOR", "")  # "Name <email>"
    if "<" in raw and raw.endswith(">"):
        name, _, email = raw[:-1].partition("<")
        return Actor(name.strip(), email.strip())
    name, email = _git_config("user.name"), _git_config("user.email")
    if not (name and email):
        raise CliError("cannot tell who is writing: set STRATEGY_ACTOR='Name <email>' or git user.name/email")
    return Actor(name, email)


_SKILL_DIRS = (".opencode/skills", ".claude/skills", ".agents/skills")
_HOME_SKILL_DIRS = (".config/opencode/skills", ".claude/skills", ".agents/skills")


def _find_gen_wbs() -> Path:
    here = Path(__file__).resolve().parent.parent
    candidates = [os.environ.get("GEN_WBS_SCRIPT")]
    candidates += [Path.home() / d / "program-strategy/scripts/gen_wbs.py" for d in _HOME_SKILL_DIRS]
    candidates += [here / d / "program-strategy/scripts/gen_wbs.py" for d in _SKILL_DIRS]
    for p in candidates:
        if p and Path(p).is_file():
            return Path(p)
    raise CliError("gen_wbs.py not found (set GEN_WBS_SCRIPT)")


def _run_chart(planning_root: Path) -> None:
    script = _find_gen_wbs()
    (planning_root / "docs").mkdir(exist_ok=True)
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                            env={**os.environ, "PROJECT_DIR": str(planning_root)})
    if result.returncode != 0:
        raise CliError(f"chart generation failed: {(result.stderr or result.stdout).strip()[-300:]}")


def _pick_project(reg: Registry, key: str | None) -> str:
    key = key or os.environ.get("STRATEGY_PROJECT")
    if key:
        if key not in reg:
            raise CliError(f"unknown project {key!r}; known: {', '.join(reg.keys())}")
        return key
    if len(reg.keys()) == 1:
        return reg.keys()[0]
    raise CliError(f"several projects in the manifest; pass --project ({', '.join(reg.keys())})")


Edit = tuple[str, Callable[[str], str], bool]   # (planning file, text transform, must exist)


def _apply(reg: Registry, key: str, edits: list[Edit], message: str | Callable[[], str],
           actor: Actor, chart: bool):
    """One commit that applies every edit to the planning files of project `key`."""
    spec = reg.get(key).spec

    def fn(checkout: Path) -> None:
        for filename, text_fn, required in edits:
            target = checkout / spec.planning_file(filename)
            if not target.is_file():
                if required:
                    raise CliError(f"{key}: {spec.planning_file(filename)} does not exist")
                continue
            original = target.read_text(encoding="utf-8")
            updated = text_fn(original)
            if updated != original:
                target.write_text(updated, encoding="utf-8")
        if chart:
            _run_chart(checkout / spec.planning_dir)

    return edit(reg, key, fn, message, actor)


def _check_text(label: str, value: str, *, single_line: bool = False) -> None:
    if "|" in value:
        raise CliError(f"{label} cannot contain '|' (it would split the table row)")
    if single_line and len(value.splitlines()) > 1:
        raise CliError(f"{label} must be a single line")


def _mentions(text: str, wbs: str) -> int:
    return len(re.findall(rf"(?<![\w.]){re.escape(wbs)}(?![\w.])", text))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="strategy", description=__doc__.split("\n\n")[0])
    p.add_argument("--manifest", help="path to PROGRAM.MD (or its folder)")
    p.add_argument("--project", "-p", help="project key from the manifest")
    p.add_argument("--message", "-m", help="commit message (default: describes the change)")
    p.add_argument("--chart", action="store_true", help="also regenerate docs/wbs.* in the same commit")
    top = p.add_subparsers(dest="resource", required=True)

    feature = top.add_parser("feature").add_subparsers(dest="action", required=True)
    s = feature.add_parser("status"); s.add_argument("wbs"); s.add_argument("status", choices=list(_FEATURE_STATUSES))
    s.add_argument("--uat-approved", action="store_true",
                   help="required for 'released': confirms a human signed off in UAT")
    fa = feature.add_parser("add", help="add a feature under a sub-area; the next WBS code is assigned")
    fa.add_argument("prefix", help="sub-area WBS prefix, e.g. 3.1"); fa.add_argument("name")
    fa.add_argument("--status", choices=["idea", "in-progress"], default="idea")
    fa.add_argument("--value", type=int); fa.add_argument("--effort", type=int)
    fa.add_argument("--notes", default=""); fa.add_argument("--owner")
    fm = feature.add_parser("move", help="move a feature to another sub-area (it gets a new WBS code)")
    fm.add_argument("wbs"); fm.add_argument("target_prefix")
    n = feature.add_parser("notes"); n.add_argument("wbs"); n.add_argument("text")
    sc = feature.add_parser("score"); sc.add_argument("wbs"); sc.add_argument("value", type=int)
    sc.add_argument("effort", type=int, nargs="?")

    ib = top.add_parser("inbox").add_subparsers(dest="action", required=True)
    ib.add_parser("list").add_argument("--all", action="store_true", help="include archived items")
    sub = ib.add_parser("submit"); sub.add_argument("type", choices=list(_inbox.KINDS)); sub.add_argument("title")
    sub.add_argument("--body", default=""); sub.add_argument("--severity"); sub.add_argument("--wbs")
    sub.add_argument("--as", dest="submitter", help="submitter name (default: the actor)")
    pr = ib.add_parser("promote"); pr.add_argument("id")
    pr.add_argument("--prefix", help="WBS sub-area for a feature request, e.g. 1.2"); pr.add_argument("--value", type=int)
    dc = ib.add_parser("decline"); dc.add_argument("id"); dc.add_argument("--reason", required=True)
    du = ib.add_parser("duplicate"); du.add_argument("id"); du.add_argument("--of", required=True, help="existing bug id or WBS")
    ib.add_parser("sweep", help="archive items untriaged for %d days" % _inbox.ARCHIVE_DAYS)

    top.add_parser("pr", help="open, or refresh, the pull request from the project's planning branch to its default branch")

    dp = top.add_parser("deps", help="list the projects a project depends on (--up: that depend on it), depth-first")
    dp.add_argument("project"); dp.add_argument("--up", action="store_true")
    dp.add_argument("--depth", type=int, default=MAX_DEPTH, help=f"levels to follow, at most {MAX_DEPTH}")

    md = top.add_parser("merge-driver").add_subparsers(dest="action", required=True)
    mi = md.add_parser("install", help="register the field-level merge driver in the current git repository")
    mi.add_argument("--repo", default=".")

    bug = top.add_parser("bug").add_subparsers(dest="action", required=True)
    a = bug.add_parser("add"); a.add_argument("title"); a.add_argument("--severity", choices=list(_SEVERITIES), default="medium")
    a.add_argument("--notes", default=""); a.add_argument("--wbs"); a.add_argument("--owner")
    st = bug.add_parser("status"); st.add_argument("id", type=int); st.add_argument("status", choices=list(_BUG_STATUSES))
    st.add_argument("--fix", dest="fix_version"); st.add_argument("--notes")
    c = bug.add_parser("close"); c.add_argument("id", type=int); c.add_argument("--resolved-in", default="")
    return p


DRIVER_NAME = "strategy-rows"
_ATTRIBUTES = ("PRODUCT.MD", "BUGS.MD")


def install_merge_driver(repo: Path) -> str:
    """Point git at the field-level merge driver for PRODUCT.MD and BUGS.MD.

    The git config is local to the clone (each developer runs this once); the
    .gitattributes lines are committed so the file names are tracked with the code.
    """
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, capture_output=True, text=True)
    if top.returncode != 0:
        raise CliError(f"{repo} is not inside a git repository")
    root = Path(top.stdout.strip())
    driver = Path(__file__).resolve().with_name("mergedriver.py")
    command = f'"{sys.executable}" "{driver}" %O %A %B %P'
    for key, value in ((f"merge.{DRIVER_NAME}.name", "Field-level merge for strategy-as-code tables"),
                       (f"merge.{DRIVER_NAME}.driver", command)):
        subprocess.run(["git", "config", "--local", key, value], cwd=root, check=True)
    attrs = root / ".gitattributes"
    existing = attrs.read_text(encoding="utf-8") if attrs.exists() else ""
    missing = [f"{name} merge={DRIVER_NAME}" for name in _ATTRIBUTES
               if f"{name} merge={DRIVER_NAME}" not in existing]
    if missing:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        attrs.write_text(existing + sep + "\n".join(missing) + "\n", encoding="utf-8")
    return (f"merge driver '{DRIVER_NAME}' configured in {root}"
            + (f"; added {len(missing)} line(s) to .gitattributes (commit it)" if missing else "; .gitattributes already set"))


def _run_deps(manifest: Path, args: argparse.Namespace) -> str:
    reg = Registry.from_manifest(manifest)
    reg.expand()
    visits = walk(reg.specs(), args.project, "up" if args.up else "down", args.depth)
    lines = []
    for v in visits:
        note = " (already listed above)" if v.repeat else f" (more beyond {args.depth} levels)" if v.truncated else ""
        lines.append(f"{'  ' * v.depth}{v.key}{note}")
    return "\n".join(lines)


def _run_pr(reg: Registry, key: str) -> str:
    project = reg.refresh(key)
    if project.spec.mode != "branch":
        raise CliError(f"{key} is in {project.spec.mode} mode; only branch-mode projects have a planning branch to propose")
    if not os.environ.get("GIT_TOKEN"):
        raise CliError("set GIT_TOKEN (a token that can open pull requests) to talk to GitHub")
    if pullrequest.github_repo(project.spec.repo) is None:
        raise CliError(f"{key}: {project.spec.repo} is not a github.com repository")
    with project.locked():
        base = project.default_branch()
        commits = pullrequest.waiting(project, base)
    pr = pullrequest.ensure_pull_request(project, base, commits)
    if pr is None:
        return f"{key}: nothing waiting on {project.target_branch}, no pull request opened"
    return f"{key}: {'opened' if pr.created else 'refreshed'} pull request #{pr.number}: {pr.url}"


def run(args: argparse.Namespace) -> str:
    if args.resource == "merge-driver":
        return install_merge_driver(Path(args.repo))
    if args.resource == "deps":
        return _run_deps(find_manifest(args.manifest), args)
    reg = Registry.from_manifest(find_manifest(args.manifest))
    reg.expand()
    key = _pick_project(reg, args.project)
    if args.resource == "pr":
        return _run_pr(reg, key)
    actor = resolve_actor()
    if args.resource == "inbox":
        return _run_inbox(reg, key, actor, args)
    msg = args.message
    chart = args.chart

    notes: list[str] = []          # extra lines for the caller; refilled on every attempt
    label = lambda: summary        # noqa: E731 - message is read after the edit has run

    if args.resource == "feature":
        if args.action == "status":
            if args.status == "released" and not args.uat_approved:
                raise CliError("'released' is the human UAT sign-off step; pass --uat-approved to confirm it")
            status = _FEATURE_STATUSES[args.status]
            edits = [("PRODUCT.MD", lambda t: product_parser.transform_feature_status(t, args.wbs, status), True)]
            summary = f"feature {args.wbs} -> {status.value}"
        elif args.action == "notes":
            edits = [("PRODUCT.MD", lambda t: product_parser.transform_feature_notes(t, args.wbs, args.text), True)]
            summary = f"feature {args.wbs} notes updated"
        elif args.action == "score":
            edits = [("PRODUCT.MD", lambda t: product_parser.transform_feature_score(t, args.wbs, args.value, args.effort), True)]
            summary = f"feature {args.wbs} score {args.value}/{args.effort if args.effort is not None else '-'}"
        elif args.action == "add":
            _check_text("feature name", args.name, single_line=True)
            _check_text("feature notes", args.notes)
            req = NewFeature(wbs_prefix=args.prefix, name=args.name, status=_FEATURE_STATUSES[args.status],
                             value=args.value, effort=args.effort, notes=args.notes, owner=args.owner)
            summary = f"feature added: {args.name}"

            def add(text: str) -> str:
                nonlocal summary
                new_text, feature = product_parser.transform_add_feature(text, req)
                summary = f"feature {feature.wbs} added: {args.name}"
                return new_text

            edits = [("PRODUCT.MD", add, True)]
        else:  # move
            old, target = args.wbs, args.target_prefix
            summary = f"feature {old} moved"

            def move_product(text: str) -> str:
                nonlocal summary
                new_text, moved = product_parser.transform_move_feature(text, old, target)
                new = moved.wbs
                summary = f"feature {old} moved to {new}"
                state["new"] = new
                left = _mentions(new_text, old)
                notes[:] = [f"{left} other mention(s) of {old} remain in PRODUCT.MD (notes, Known Gaps); review them"] if left else []
                notes.append(f"initiatives in other projects or the manifest that cite {key}:{old} are not updated")
                return new_text

            state: dict[str, str] = {}
            edits = [
                ("PRODUCT.MD", move_product, True),
                ("ABOUT.MD", lambda t: about_parser.transform_rewrite_wbs_refs(t, old, state["new"]), False),
                ("BUGS.MD", lambda t: bugs_parser.transform_rewrite_wbs_refs(t, old, state["new"]), False),
            ]
    else:
        if args.action == "add":
            req = BugCreate(title=args.title, severity=_SEVERITIES[args.severity], notes=args.notes,
                            wbs_ref=args.wbs, owner=args.owner)
            edits = [("BUGS.MD", lambda t: bugs_parser.transform_add_bug(t, req)[0], True)]
            summary = f"bug added: {args.title}"
        elif args.action == "status":
            req = BugUpdate(status=_BUG_STATUSES[args.status], fix_version=args.fix_version, notes=args.notes)
            edits = [("BUGS.MD", lambda t: bugs_parser.transform_update_bug(t, args.id, req)[0], True)]
            summary = f"bug {args.id} -> {req.status.value}"
        else:
            edits = [("BUGS.MD", lambda t: bugs_parser.transform_close_bug(t, args.id, args.resolved_in), True)]
            summary = f"bug {args.id} closed"

    result = _apply(reg, key, edits, msg or label, actor, chart)
    where = f"{result.branch} @ {result.sha[:7]}" if result.changed else "no change"
    return "\n".join([f"{key}: {summary} ({where})", *[f"  note: {n}" for n in notes]])


def _run_inbox(reg: Registry, key: str, actor: Actor, args: argparse.Namespace) -> str:
    spec = reg.get(key).spec
    out: list[str] = []

    def change(fn: Callable[[Path], None], message: str) -> str:
        res = edit(reg, key, lambda checkout: fn(checkout / spec.planning_dir), message, actor)
        return f"{key}: {message} ({res.branch} @ {res.sha[:7]})" if res.changed else f"{key}: no change"

    if args.action == "list":
        reg.refresh(key)
        items = _inbox.list_items(reg.get(key).planning_root, include_archive=args.all)
        if not items:
            return f"{key}: inbox is empty"
        return "\n".join(f"{i.id}  {i.kind:<7} {'STALE ' if i.stale else ''}{'ARCHIVED ' if i.archived else ''}"
                         f"{i.title}  ({i.submitter}, {i.age_days}d)" for i in items)
    try:
        if args.action == "submit":
            who = args.submitter or actor.name
            made: dict[str, str] = {}

            def add(root: Path) -> None:
                made["id"] = _inbox.write_item(root, args.type, args.title, args.body, who,
                                               args.severity or "", args.wbs or "").id
            line = change(add, f"inbox: {args.type} submitted: {args.title}")
            return f"{line}\n  id: {made.get('id', '?')}"
        if args.action == "promote":
            made = {}

            def pro(root: Path) -> None:
                made["what"] = _inbox.promote(root, args.id, args.prefix or "", args.value)
            line = change(pro, f"inbox: promote {args.id}")
            return f"{line}\n  became: {made.get('what')}"
        if args.action == "decline":
            return change(lambda root: _inbox.decline(root, args.id, reason=args.reason or ""), f"inbox: decline {args.id}: {args.reason}")
        if args.action == "duplicate":
            return change(lambda root: _inbox.decline(root, args.id, duplicate_of=args.of or ""), f"inbox: duplicate {args.id} of {args.of}")
        moved: list[str] = []
        line = change(lambda root: moved.extend(_inbox.sweep(root)), "inbox: archive stale items")
        return f"{line}\n  archived: {len(moved)}"
    except _inbox.InboxError as exc:
        raise CliError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    try:
        print(run(build_parser().parse_args(argv)))
        return 0
    except (CliError, ManifestError, RegistryError, ValueError) as exc:
        print(f"strategy: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
