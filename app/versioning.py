"""
Semantic version calculation for strategy-as-code.

Rules (from the program-strategy skill):
  MAJOR.MINOR.RELEASE
  - Each Initiative is tagged Major or Minor. When every feature in an
    Initiative reaches Live/Released, it "completes" and drives the next
    release's bump - Major initiatives bump MAJOR, Minor initiatives bump
    MINOR. The highest tier among completed initiatives wins.
  - RELEASE increments for bug-fix / hotfix releases when no Initiative completes.
  - MAJOR also becomes 1.0.0 once all product scope is complete, regardless
    of Initiative tags.
  - Version numbers are assigned at release time; do not pre-assign them.
"""

from __future__ import annotations
import re
from .models import AboutDoc, FeatureStatus, ProductDoc


def _ver(version: str) -> tuple[int, int, int]:
    m = re.match(r"v?(\d+)[._-](\d+)(?:[._-](\d+))?", version.strip())
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    return (0, 0, 0)


def _completed_initiatives(about: AboutDoc, product: ProductDoc | None):
    """Initiatives whose every listed feature is Live/Released, split by tier."""
    major, minor = [], []
    if not product:
        return major, minor
    all_features = {
        f.wbs: f
        for area in product.wbs_areas
        for sa in area.sub_areas
        for f in sa.features
    }
    done = {FeatureStatus.live, FeatureStatus.released}
    for ini in about.initiatives:
        if not ini.items:
            continue
        if all(all_features.get(w) is not None and all_features[w].status in done for w in ini.items):
            (major if ini.kind == "major" else minor).append(ini.name)
    return major, minor


def next_release_version(about: AboutDoc, product: ProductDoc | None = None) -> str:
    """
    Compute the next version number given the current changelog and product state.

    - If there is an in-progress changelog entry, that version IS the pending release.
    - Otherwise: a completed Major initiative → MAJOR + 1, MINOR/RELEASE = 0
    - Otherwise: a completed Minor initiative → MINOR + 1, RELEASE = 0
    - Otherwise: MINOR unchanged, RELEASE + 1 (bug-fix release)
    - If all product scope is complete → 1.0.0
    """
    # An in-progress entry means a version has been opened but not yet cut.
    for entry in about.changelog:
        if entry.in_progress:
            return entry.version

    # No open release: find the highest shipped version and compute the next one.
    latest_major = latest_minor = latest_release = 0
    for entry in about.changelog:
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)", entry.version)
        if m:
            maj, min_, rel = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if (maj, min_, rel) > (latest_major, latest_minor, latest_release):
                latest_major, latest_minor, latest_release = maj, min_, rel

    major_done, minor_done = _completed_initiatives(about, product)

    if major_done:
        computed = f"{latest_major + 1}.0.0"
    elif minor_done:
        computed = f"{latest_major}.{latest_minor + 1}.0"
    else:
        computed = f"{latest_major}.{latest_minor}.{latest_release + 1}"

    if product and product.overall_completion_pct >= 1.0:
        return "1.0.0"

    return computed


def version_rationale(about: AboutDoc, product: ProductDoc | None = None) -> str:
    """Human-readable explanation of what's going into the next release."""
    # If there's an open in-progress entry, describe its groups.
    for entry in about.changelog:
        if entry.in_progress:
            labels = [g.label for g in entry.groups if g.label.lower() != "bug fixes"]
            if not labels:
                return "Bug fixes only"
            if len(labels) == 1:
                return f"Shipping: {labels[0]}"
            return f"Shipping: {', '.join(labels)}"

    # No open release: describe which initiatives are driving the bump.
    major_done, minor_done = _completed_initiatives(about, product)
    if major_done:
        word = "initiative" if len(major_done) == 1 else "initiatives"
        return f"Major {word} complete: {', '.join(major_done)}"
    if minor_done:
        word = "initiative" if len(minor_done) == 1 else "initiatives"
        return f"Minor {word} complete: {', '.join(minor_done)}"
    return "No initiatives complete - this would be a bug-fix release."
