"""One feature action applied to features in several member projects.

The targets are grouped by project and each group is one `writer.edit`: one commit
and push per repository, as that project's own `/p/<key>/api` route would make. A
project that fails (unreadable, an unknown WBS code, a push that keeps losing)
leaves its repository untouched and is reported; the others still go through.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .models import FeatureStatus
from .parsers import product as product_parser
from .registry import Registry
from .writer import Actor, edit


@dataclass(frozen=True)
class FeatureAction:
    status: FeatureStatus | None = None
    owner: str | None = None
    flagged: bool | None = None

    def is_empty(self) -> bool:
        return self.status is None and self.owner is None and self.flagged is None

    def apply(self, text: str, wbs: str) -> str:
        if self.status is not None:
            text = product_parser.transform_feature_status(text, wbs, self.status)
            # Same rule as a single edit: shipping a feature resolves its gap flag unless this action sets it.
            if self.status in (FeatureStatus.live, FeatureStatus.released) and self.flagged is None:
                text = product_parser.transform_feature_flagged(text, wbs, False)
        if self.owner is not None:
            text = product_parser.transform_feature_owner(text, wbs, self.owner)
        if self.flagged is not None:
            text = product_parser.transform_feature_flagged(text, wbs, self.flagged)
        return text

    def describe(self) -> str:
        parts = []
        if self.status is not None:
            parts.append(f"status {self.status.value}")
        if self.owner is not None:
            parts.append(f"owner {self.owner or 'cleared'}")
        if self.flagged is not None:
            parts.append("flagged" if self.flagged else "unflagged")
        return ", ".join(parts)


@dataclass
class ProjectResult:
    project: str
    wbs: list[str]
    ok: bool
    changed: bool = False
    sha: str = ""
    error: str = ""

    def as_dict(self) -> dict:
        return {"project": self.project, "wbs": self.wbs, "ok": self.ok, "changed": self.changed,
                "sha": self.sha, "error": self.error}


def _group(targets: list[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for project, wbs in targets:
        if wbs not in grouped.setdefault(project, []):
            grouped[project].append(wbs)
    return grouped


def run(registry: Registry, targets: list[tuple[str, str]], action: FeatureAction,
        actor: Actor | None = None) -> list[ProjectResult]:
    """Apply `action` to every (project, wbs) target; one result per project, in target order."""
    grouped = _group(targets)

    def one(item: tuple[str, list[str]]) -> ProjectResult:
        key, codes = item
        if key not in registry:
            return ProjectResult(key, codes, False, error=f"unknown project {key!r}")
        spec = registry.get(key).spec

        def apply(checkout) -> None:
            target = checkout / spec.planning_dir / "PRODUCT.MD"
            if not target.is_file():
                raise FileNotFoundError(f"{key}: PRODUCT.MD does not exist")
            original = text = target.read_text(encoding="utf-8")
            for wbs in codes:
                text = action.apply(text, wbs)
            if text != original:
                target.write_text(text, encoding="utf-8")

        message = lambda: f"UI: {', '.join(codes)} {action.describe()}"  # noqa: E731
        try:
            written = edit(registry, key, apply, message, actor)
        except Exception as exc:  # reported per project; never stops the others
            return ProjectResult(key, codes, False, error=str(exc))
        return ProjectResult(key, codes, True, written.changed, written.sha)

    if not grouped:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(grouped))) as pool:
        return list(pool.map(one, grouped.items()))
