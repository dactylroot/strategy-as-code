"""Write the program manifest's Initiatives section.

When PROGRAM.MD lives in a repository the registry clones (`Registry.from_manifest_repo`), a change is
one `writer.edit` commit by the signed-in person, like a change to any member project's planning files.
When the manifest is a plain local file (development, `PROGRAM_MANIFEST=path`) the same change is made in
place under a file lock; that leaves no history, so the result says it was not versioned.

Only the transforms in `manifest.py` are applied here, and they only touch the `## Initiatives` section, so
this can never change which repositories the service clones and pushes to: the Projects table is changed by
a person editing the manifest repository.
"""
from __future__ import annotations
import fcntl
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import registry as registry_mod
from .registry import Registry, RegistryError
from .manifest import ManifestError
from .writer import Actor, EditAborted, edit


class ManifestWriteError(RegistryError):
    pass


@dataclass
class ManifestWrite:
    changed: bool
    versioned: bool      # False when written in place to a local file
    sha: str = ""
    branch: str = ""


def edit_manifest(registry: Registry, transform: Callable[[str], str], message: str,
                  actor: Actor | None = None) -> ManifestWrite:
    """Apply `transform` (text in, text out; e.g. `manifest.transform_add_initiative` with its
    arguments bound) to the manifest and save it. `transform` may run more than once on a repository
    (once per push attempt), so it must work from the text it is given. A ManifestError from it
    abandons the write."""
    project = registry.manifest_project
    if project is not None:
        name = registry._manifest_name

        def apply(checkout: Path) -> None:
            target = checkout / project.spec.planning_dir / name
            if not target.is_file():
                raise ManifestWriteError(f"{name} does not exist in {project.spec.repo}")
            original = target.read_text(encoding="utf-8")
            try:
                updated = transform(original)
            except ManifestError as exc:           # the request was wrong, the clone is fine
                raise EditAborted() from exc
            if updated != original:
                target.write_text(updated, encoding="utf-8")

        try:
            written = edit(registry, registry_mod.MANIFEST_KEY, apply, message, actor)
        except EditAborted as aborted:
            raise aborted.__cause__ from None
        return ManifestWrite(written.changed, True, written.sha, written.branch)
    path = registry.manifest_path
    if path is None:
        raise ManifestWriteError("this program has no manifest file to write")
    return _edit_local(Path(path), transform)


def _edit_local(path: Path, transform: Callable[[str], str]) -> ManifestWrite:
    lock = path.with_name(f".{path.name}.lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        original = path.read_text(encoding="utf-8")
        updated = transform(original)
        if updated == original:
            return ManifestWrite(False, False)
        tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False)
        try:
            with tmp:
                tmp.write(updated)
            os.chmod(tmp.name, path.stat().st_mode & 0o777)
            os.replace(tmp.name, path)
        except BaseException:
            Path(tmp.name).unlink(missing_ok=True)
            raise
        return ManifestWrite(True, False)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
