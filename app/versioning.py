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

    - If In Progress has any WBS sub-areas → MINOR + 1, RELEASE = 0
    - If In Progress is empty → MINOR unchanged, RELEASE + 1  (bug-fix release)
    - If all product scope is complete after this release → 1.0.0
    """
    # Find the highest version across ALL changelog entries (including in-progress).
    # An in-progress entry means that version number is already committed — the
    # next cut must produce a higher version than anything already in the changelog.
    latest_major = latest_minor = latest_release = 0
    for entry in about.changelog:
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)", entry.version)
        if m:
            maj, min_, rel = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if (maj, min_, rel) > (latest_major, latest_minor, latest_release):
                latest_major, latest_minor, latest_release = maj, min_, rel

    # Count In Progress sub-area items
    ip_section = about.roadmap_section("In Progress")
    ip_items = ip_section.items if ip_section else []
    has_sub_areas = any(item.strip() for item in ip_items)

    if has_sub_areas:
        next_minor   = latest_minor + 1
        next_release = 0
    else:
        next_minor   = latest_minor
        next_release = latest_release + 1

    next_major = latest_major

    # Check if this would complete all product scope
    if product and product.overall_completion_pct >= 1.0:
        return "1.0.0"

    return f"{next_major}.{next_minor}.{next_release}"


def version_rationale(about: AboutDoc) -> str:
    """Human-readable explanation of why the next version is what it is."""
    ip_section = about.roadmap_section("In Progress")
    ip_items   = [i for i in (ip_section.items if ip_section else []) if i.strip()]
    n = len(ip_items)
    if n == 0:
        return "No sub-areas in progress — this would be a bug-fix release."
    if n == 1:
        return f"1 sub-area shipping: {ip_items[0]}"
    return f"{n} sub-areas shipping: {', '.join(ip_items)}"
