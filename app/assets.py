"""Screenshots live outside the project's history.

A screenshot is a binary that is only wanted while its bug is open, and git keeps
every blob it was ever given. So the files below the planning directory's
`.planning/inbox/` and `.planning/screenshots/` are excluded from commits (the
clone's own `info/exclude`, nothing is committed to the project) and are kept on a
ref of their own, REF. That ref is outside `refs/heads`, so a default clone does
not download it, and it is always a single parentless commit holding exactly the
screenshots that exist now. Removing a file is therefore a new commit without it,
force-pushed over the old one, and the old blob is no longer reachable.

The files sit in the working tree where the inbox and bug code already expect
them. `restore` makes the tree match the ref after every sync; `publish` pushes
what a write changed, after its code commit is pushed.
"""
from __future__ import annotations
import io
import tarfile
from pathlib import Path

from .registry import GitError, Project, run_git, run_git_bytes

REF = "refs/planning/assets"
IMAGE_EXTS = ("png", "jpg", "jpeg", "gif")
EXCLUDE = ["**/.planning/screenshots/"] + [f"**/.planning/inbox/**/*.{e}" for e in IMAGE_EXTS]
_COMMITTER = {"GIT_AUTHOR_NAME": "strategy-as-code", "GIT_AUTHOR_EMAIL": "noreply@strategy-as-code",
              "GIT_COMMITTER_NAME": "strategy-as-code", "GIT_COMMITTER_EMAIL": "noreply@strategy-as-code"}
MAX_PUSH_ATTEMPTS = 3


def _candidates(root: Path) -> list[Path]:
    found: list[Path] = []
    shots = root / ".planning" / "screenshots"
    if shots.is_dir():
        found += [p for p in shots.rglob("*") if p.is_file()]
    inbox = root / ".planning" / "inbox"
    if inbox.is_dir():
        found += [p for p in inbox.rglob("*") if p.is_file() and p.suffix.lower().lstrip(".") in IMAGE_EXTS]
    return found


def _tracked(project: Project) -> set[str]:
    """Paths (relative to the planning root) that older versions committed; they stay ordinary tracked files."""
    prefix = project.spec.planning_dir.strip("/")
    out = project.git("ls-files", "-z", "--", ".planning", check=False) if not prefix else \
        project.git("ls-files", "-z", "--", f"{prefix}/.planning", check=False)
    names = [n for n in out.split("\0") if n]
    return {n[len(prefix) + 1:] if prefix else n for n in names}


def _local(project: Project) -> dict[str, Path]:
    root = project.planning_root
    tracked = _tracked(project)
    return {p.relative_to(root).as_posix(): p for p in _candidates(root)
            if p.relative_to(root).as_posix() not in tracked}


def _fetch(project: Project) -> str:
    """Bring the local copy of REF to the remote's; '' when the remote has none."""
    project.git("fetch", "-q", "origin", f"+{REF}:{REF}", check=False)
    if not project.git("ls-remote", "origin", REF):
        project.git("update-ref", "-d", REF, check=False)
        return ""
    return project.git("rev-parse", "--verify", "-q", REF, check=False)


def _tree(project: Project, tip: str) -> dict[str, str]:
    if not tip:
        return {}
    out = project.git("ls-tree", "-r", "-z", tip, check=False)
    entries = {}
    for line in out.split("\0"):
        if line:
            meta, _, path = line.partition("\t")
            entries[path] = meta.split()[2]
    return entries


def restore(project: Project) -> None:
    """Make the screenshot files in the working tree exactly the ref's. Caller holds the project lock."""
    try:
        tip = _fetch(project)
    except GitError:
        return      # remote unreachable: keep what the tree has
    tree = _tree(project, tip)
    for path in _local(project).values():
        path.unlink()
    if tip:
        data = run_git_bytes(project.path, ["archive", "--format=tar", tip])
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    target = project.planning_root / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(tar.extractfile(member).read())
    project.assets_base, project.assets_tip = tree, tip


def _hash(project: Project, path: Path) -> str:
    return project.git("hash-object", "-w", str(path))


def publish(project: Project) -> bool:
    """Push the screenshots a write added, replaced or removed. Returns True when the ref changed.
    Caller holds the project lock. Only this write's own changes are applied, on top of whatever
    the ref holds now, so two writers do not drop each other's files."""
    local = {rel: _hash(project, p) for rel, p in _local(project).items()}
    base = project.assets_base
    changed = {rel: sha for rel, sha in local.items() if base.get(rel) != sha}
    removed = [rel for rel in base if rel not in local]
    if not changed and not removed:
        return False
    for attempt in range(MAX_PUSH_ATTEMPTS):
        tip = _fetch(project)
        tree = {**{k: v for k, v in _tree(project, tip).items() if k not in removed}, **changed}
        if tree == _tree(project, tip):
            project.assets_base, project.assets_tip = tree, tip
            return False
        try:
            if not tree:
                project.git("push", "origin", f":{REF}", f"--force-with-lease={REF}:{tip}")
                project.git("update-ref", "-d", REF, check=False)
                new = ""
            else:
                index = project.path / ".git" / "assets-index"
                env = {"GIT_INDEX_FILE": str(index)}
                index.unlink(missing_ok=True)
                try:
                    project.git("update-index", "--index-info", env=env,
                                input="".join(f"100644 {sha}\t{rel}\n" for rel, sha in sorted(tree.items())))
                    tree_sha = project.git("write-tree", env=env)
                finally:
                    index.unlink(missing_ok=True)
                new = project.git("commit-tree", tree_sha, "-m", "screenshots", env=_COMMITTER)
                project.git("push", "origin", f"{new}:{REF}", f"--force-with-lease={REF}:{tip}")
                project.git("update-ref", REF, new)
            project.assets_base, project.assets_tip = tree, new
            return True
        except GitError as exc:
            if attempt == MAX_PUSH_ATTEMPTS - 1 or not any(
                    t in str(exc) for t in ("stale info", "rejected", "failed to update ref", "fetch first")):
                raise
    return False
