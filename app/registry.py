"""Project registry: one managed git clone per member project.

The clone is a cache of the remote, never a source of truth. It is always
reset to the remote tip before it is read or written, so it must not be a
developer's own working checkout. For a local-path project the registry
clones from that path into its own cache directory.
"""
from __future__ import annotations
import fcntl
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import DEFAULT_PLANNING_BRANCH, ProgramInitiative, ProjectSpec, load_initiatives, load_manifest


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


def run_git(cwd: Path | None, args: list[str], *, env: dict | None = None, check: bool = True) -> str:
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
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
                 committer: tuple[str, str] = ("strategy-as-code", "noreply@strategy-as-code")):
        self.committer = committer
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self._projects = {s.key: Project(spec=s, path=self.cache_dir / s.key) for s in specs}
        self.manifest_path: Path | None = None

    @classmethod
    def from_manifest(cls, manifest_path: Path, cache_dir: Path | None = None) -> "Registry":
        reg = cls(load_manifest(manifest_path), cache_dir)
        reg.manifest_path = Path(manifest_path)
        return reg

    def initiatives(self) -> list[ProgramInitiative]:
        """Program-level initiatives, read from the manifest each call so edits
        show without a restart. Empty when the registry was not built from a file."""
        if self.manifest_path is None:
            return []
        return load_initiatives(self.manifest_path, set(self._projects))

    def keys(self) -> list[str]:
        return list(self._projects)

    def __contains__(self, key: str) -> bool:
        return key in self._projects

    def get(self, key: str) -> Project:
        try:
            return self._projects[key]
        except KeyError:
            raise RegistryError(f"unknown project {key!r}") from None

    def ensure_clone(self, key: str) -> Project:
        project = self.get(key)
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

    def refresh(self, key: str) -> Project:
        """Bring one project's clone up to date. A failure is recorded in its
        status and re-raised; it never touches another project."""
        project = self.ensure_clone(key)
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
