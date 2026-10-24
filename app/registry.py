"""Project registry: one managed git clone per member project.

The clone is a cache of the remote, never a source of truth. It is always
reset to the remote tip before it is read or written, so it must not be a
developer's own working checkout. For a local-path project the registry
clones from that path into its own cache directory.
"""
from __future__ import annotations
import base64
import fcntl
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import (DEFAULT_PLANNING_BRANCH, ManifestError, ProgramInitiative, ProjectSpec, load_initiatives,
                       load_manifest, nest, parse_manifest, repo_identity, validate)


class RegistryError(RuntimeError):
    pass


class SyncConflict(RegistryError):
    """The remote's default branch cannot be merged into the planning branch."""


class GitError(RegistryError):
    def __init__(self, args: list[str], stderr: str):
        self.git_args = args
        self.stderr = stderr
        super().__init__(f"git {' '.join(args)} failed: {stderr}")


def default_cache_dir() -> Path:
    env = os.environ.get("STRATEGY_CACHE_DIR")
    return Path(env) if env else Path.home() / ".cache" / "strategy-as-code" / "projects"


def _token_env(base: dict) -> dict:
    """Authenticate https://github.com remotes with $GIT_TOKEN, for every git call.

    The token travels as an http extra header set through GIT_CONFIG_* (never in a
    remote URL, a file or a command line), so it stays out of `git remote -v`,
    process listings and error messages. Other hosts and ssh remotes are untouched.
    """
    token = base.get("GIT_TOKEN", "")
    if not token:
        return {}
    n = int(base.get("GIT_CONFIG_COUNT", "0") or 0)
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": str(n + 1),
        f"GIT_CONFIG_KEY_{n}": "http.https://github.com/.extraheader",
        f"GIT_CONFIG_VALUE_{n}": f"Authorization: basic {basic}",
    }


def run_git(cwd: Path | None, args: list[str], *, env: dict | None = None, check: bool = True) -> str:
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    full_env.update(_token_env(full_env))
    full_env.update(env or {})
    result = subprocess.run(["git", *args], cwd=cwd, env=full_env, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise GitError(args, (result.stderr or result.stdout).strip())
    return result.stdout.strip()


@dataclass
class SyncStatus:
    last_fetch: float | None = None
    last_push: float | None = None
    sha: str | None = None
    last_ok: bool | None = None
    last_error: str | None = None
    pushes: int = 0
    pr_url: str | None = None      # open pull request for the planning branch, once known


@dataclass
class Project:
    spec: ProjectSpec
    path: Path
    status: SyncStatus = field(default_factory=SyncStatus)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def planning_root(self) -> Path:
        """Folder in the clone that holds PRODUCT.MD, BUGS.MD, ABOUT.MD."""
        return self.path / self.spec.planning_dir

    @property
    def target_branch(self) -> str | None:
        """Branch edits are written to; None means the remote's default branch."""
        if self.spec.branch:
            return self.spec.branch
        return DEFAULT_PLANNING_BRANCH if self.spec.mode == "branch" else None

    @contextmanager
    def locked(self):
        """Serialize work on this clone across threads and processes."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.path.parent / f".{self.key}.lock"
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def git(self, *args: str, check: bool = True, env: dict | None = None) -> str:
        return run_git(self.path, list(args), check=check, env=env)

    def default_branch(self) -> str:
        ref = self.git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
        if ref.startswith("origin/"):
            return ref[len("origin/"):]
        # origin/HEAD is not always set (e.g. an empty-then-pushed bare repo); ask the remote.
        out = self.git("ls-remote", "--symref", "origin", "HEAD", check=False)
        for line in out.splitlines():
            if line.startswith("ref:"):
                return line.split()[1].removeprefix("refs/heads/")
        for candidate in ("main", "master"):
            if self.git("rev-parse", "--verify", "--quiet", f"origin/{candidate}", check=False):
                return candidate
        raise RegistryError(f"cannot determine default branch of {self.key}")

    def _remote_has(self, branch: str) -> bool:
        return bool(self.git("rev-parse", "--verify", "--quiet", f"origin/{branch}", check=False))

    def sync(self) -> str:
        """Fetch and hard-reset the clone to the remote tip of its target branch.

        Caller must hold `locked()`. In branch mode the planning branch is created
        from the default branch if absent, and the default branch is merged into it
        so human edits made there are never lost; an unmergeable result raises.
        Returns the resulting HEAD sha.
        """
        self.git("fetch", "--prune", "origin")
        base = self.default_branch()
        target = self.target_branch or base
        source = f"origin/{target}" if self._remote_has(target) else f"origin/{base}"
        self.git("checkout", "-q", "-B", target, source)
        self.git("reset", "-q", "--hard", source)
        self.git("clean", "-qfd")
        if target != base and self._remote_has(base):
            if self.git("rev-list", "--count", f"HEAD..origin/{base}") != "0":
                merged = subprocess.run(
                    ["git", "merge", "--no-edit", "-q", f"origin/{base}"], cwd=self.path,
                    capture_output=True, text=True,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
                if merged.returncode != 0:
                    self.git("merge", "--abort", check=False)
                    self.git("reset", "-q", "--hard", source)
                    raise SyncConflict(
                        f"{self.key}: cannot merge {base} into {target}: "
                        f"{(merged.stderr or merged.stdout).strip()}")
        sha = self.git("rev-parse", "HEAD")
        self.status.sha = sha
        self.status.last_fetch = time.time()
        return sha


class Registry:
    def __init__(self, specs: list[ProjectSpec], cache_dir: Path | None = None,
                 committer: tuple[str, str] = ("strategy-as-code", "noreply@strategy-as-code"),
                 nested_depth: int = 3):
        self.committer = committer
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self._declared = list(specs)
        self._projects = {s.key: Project(spec=s, path=self.cache_dir / s.key) for s in specs}
        self.manifest_path: Path | None = None
        self.nested_depth = nested_depth       # levels of program-within-program; 0 turns nesting off
        self.nested_errors: dict[str, str] = {}  # nested program key -> why it could not be expanded

    @classmethod
    def from_manifest(cls, manifest_path: Path, cache_dir: Path | None = None, nested_depth: int = 3) -> "Registry":
        reg = cls(load_manifest(manifest_path), cache_dir, nested_depth=nested_depth)
        reg.manifest_path = Path(manifest_path)
        return reg

    def initiatives(self) -> list[ProgramInitiative]:
        """Program-level initiatives, read from the manifest each call so edits
        show without a restart. Empty when the registry was not built from a file."""
        if self.manifest_path is None:
            return []
        return load_initiatives(self.manifest_path, set(self._projects))

    def keys(self) -> list[str]:
        """The projects to show and edit. A nested program is a container, not a project: its
        projects are listed here as `<program>.<project>`."""
        return [k for k, p in self._projects.items() if p.spec.kind != "program"]

    def specs(self) -> list[ProjectSpec]:
        """Every row, nested programs included, in listing order."""
        return [p.spec for p in self._projects.values()]

    def __contains__(self, key: str) -> bool:
        return key in self._projects and self._projects[key].spec.kind != "program"

    def expand(self) -> dict[str, str]:
        """Read the PROGRAM.MD of every nested program (kind `program`) and list its projects here as
        `<program>.<project>`, to the depth allowed. Call at start-up and on every poll so changes to a
        nested manifest show without a restart. A program that cannot be read, would contain itself
        (same repository and folder further up the chain), or nests deeper than `nested_depth` is left
        out and named in the result and in `nested_errors`; nothing else is affected. Blocking."""
        errors: dict[str, str] = {}
        flat: list[ProjectSpec] = []
        made: dict[str, Project] = {}      # containers touched this pass, so their clone state is kept

        def visit(spec: ProjectSpec, chain: tuple[str, ...], depth: int) -> None:
            flat.append(spec)
            if spec.kind != "program":
                return
            ident = repo_identity(spec.repo, spec.planning_dir)
            if ident in chain:
                errors[spec.key] = f"{spec.key} lists itself: {spec.repo} ({spec.planning_dir}) is already above it"
                return
            if depth >= self.nested_depth:
                errors[spec.key] = (f"{spec.key} is nested more than {self.nested_depth} level(s) deep "
                                    "(PROGRAM_NESTED_DEPTH)")
                return
            project = self._projects.get(spec.key)
            if project is None or project.spec != spec:
                project = Project(spec=spec, path=self.cache_dir / spec.key)
            made[spec.key] = project
            try:
                self._refresh(project)
                manifest = project.planning_root / "PROGRAM.MD"
                if not manifest.is_file():
                    raise ManifestError(f"no PROGRAM.MD in {spec.planning_dir or 'the repository root'}")
                children = nest(parse_manifest(manifest.read_text(encoding="utf-8"), project.planning_root), spec.key)
            except Exception as exc:
                errors[spec.key] = str(exc) or exc.__class__.__name__
                return
            for child in children:
                visit(child, chain + (ident,), depth + 1)

        for spec in self._declared:
            visit(spec, (), 0)
        try:
            validate(flat)
        except ManifestError as exc:
            errors["*"] = str(exc)
            flat = list(self._declared)           # keep the outer program usable
        old = {**self._projects, **made}
        self._projects = {s.key: old[s.key] if s.key in old and old[s.key].spec == s
                          else Project(spec=s, path=self.cache_dir / s.key) for s in flat}
        self.nested_errors = errors
        return errors

    def get(self, key: str) -> Project:
        try:
            return self._projects[key]
        except KeyError:
            raise RegistryError(f"unknown project {key!r}") from None

    def ensure_clone(self, key: str) -> Project:
        return self._ensure_clone(self.get(key))

    def _ensure_clone(self, project: Project) -> Project:
        key = project.key
        with project.locked():
            if (project.path / ".git").exists():
                current = project.git("remote", "get-url", "origin", check=False)
                if current != project.spec.repo:
                    raise RegistryError(
                        f"{key}: cache clone points at {current!r}, manifest says {project.spec.repo!r}")
                self._configure(project)
                return project
            if project.path.exists() and any(project.path.iterdir()):
                raise RegistryError(f"{key}: {project.path} exists and is not a git clone")
            run_git(None, ["clone", "-q", project.spec.repo, str(project.path)])
            self._configure(project)
        return project

    def _configure(self, project: Project) -> None:
        # Identity for commits the registry itself creates (planning-branch merges).
        project.git("config", "user.name", self.committer[0])
        project.git("config", "user.email", self.committer[1])
        # Field-level merge for the planning tables, so merging the default branch
        # into the planning branch combines row edits instead of conflicting.
        # info/attributes is local to the clone; nothing is committed to the project.
        driver = Path(__file__).resolve().with_name("mergedriver.py")
        project.git("config", "merge.strategy-rows.driver", f'"{sys.executable}" "{driver}" %O %A %B %P')
        info = project.path / ".git" / "info"
        info.mkdir(exist_ok=True)
        (info / "attributes").write_text(
            "PRODUCT.MD merge=strategy-rows\nBUGS.MD merge=strategy-rows\n", encoding="utf-8")

    def refresh(self, key: str) -> Project:
        """Bring one project's clone up to date. A failure is recorded in its
        status and re-raised; it never touches another project."""
        return self._refresh(self.get(key))

    def _refresh(self, project: Project) -> Project:
        project = self._ensure_clone(project)
        with project.locked():
            try:
                project.sync()
                project.status.last_ok = True
                project.status.last_error = None
            except Exception as exc:
                project.status.last_ok = False
                project.status.last_error = str(exc)
                raise
        return project

    def status(self) -> dict[str, SyncStatus]:
        return {k: p.status for k, p in self._projects.items()}
