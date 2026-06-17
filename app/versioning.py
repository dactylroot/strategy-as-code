"""
Semantic version calculation for strategy-as-code.

Rules (from the program-strategy skill):
  MAJOR.MINOR.RELEASE
  - MINOR increments once per release that ships one or more WBS Level 2 sub-areas.
  - RELEASE increments for bug-fix / hotfix releases within a MINOR (no sub-areas shipped).
  - MAJOR stays at 0 until all product scope is complete, then becomes 1.0.0.
  - Version numbers are assigned at release time; do not pre-assign them.
"""

from __future__ import annotations
import re
from .models import AboutDoc, ProductDoc


def next_release_version(about: AboutDoc, product: ProductDoc | None = None) -> str:
    """
    Compute the next version number given the current changelog and product state.

    - If there is an in-progress changelog entry, that version IS the pending release.
    - Otherwise: if In Progress roadmap has sub-areas → MINOR + 1, RELEASE = 0
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

    ip_section = about.roadmap_section("In Progress")
    ip_items = ip_section.items if ip_section else []
    has_sub_areas = any(item.strip() for item in ip_items)

    if has_sub_areas:
        next_minor, next_release = latest_minor + 1, 0
    else:
        next_minor, next_release = latest_minor, latest_release + 1

    if product and product.overall_completion_pct >= 1.0:
        return "1.0.0"

    return f"{latest_major}.{next_minor}.{next_release}"


def version_rationale(about: AboutDoc) -> str:
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

    # No open release: describe roadmap In Progress.
    ip_section = about.roadmap_section("In Progress")
    ip_items   = [i for i in (ip_section.items if ip_section else []) if i.strip()]
    n = len(ip_items)
    if n == 0:
        return "No sub-areas in progress — this would be a bug-fix release."
    if n == 1:
        return f"1 sub-area shipping: {ip_items[0]}"
    return f"{n} sub-areas shipping: {', '.join(ip_items)}"
