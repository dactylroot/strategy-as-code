"""Does the Product Scope summary line up with the WBS?

The WBS (`## Features`: areas `### 1. Title`, sub-areas `#### 1.1 Title`) is the structure; the Product Scope
section is its plain-language summary. By convention each scope group is a WBS area (same title, same order) and
each item summarises one sub-area, ending in `(WBS 1.1)`. Groups such as Out of Scope hold what is not in the WBS.
Nothing here blocks a save: it reports what has drifted, for the Structure page and `strategy structure check`.
"""
from __future__ import annotations

from .manifest import normalize_name
from .models import FeatureStatus, ProductDoc

# Scope groups that are not WBS areas by design.
EXTRA_GROUPS = {"out of scope", "product boundaries", "non-goals", "assumptions", "constraints"}
_SHIPPED = (FeatureStatus.live, FeatureStatus.released)


def check(product: ProductDoc) -> list[str]:
    out: list[str] = []
    areas = product.wbs_areas
    for i, a in enumerate(areas, 1):
        if a.number != i:
            out.append(f"Area {a.number} {a.title!r} is not number {i}: number the areas 1 to {len(areas)} without gaps.")
        subs = [int(sa.wbs_prefix.split(".")[1]) for sa in a.sub_areas]
        for j, n in enumerate(sorted(subs), 1):
            if n != j:
                out.append(f"Area {a.number} has sub-area {a.number}.{n} but no {a.number}.{j}: "
                           f"number the sub-areas 1 to {len(subs)} (strategy structure renumber {a.number}.{n} {a.number}.{j}).")
                break
    groups = product.scope_groups
    by_title = {normalize_name(g.title): g for g in groups}
    sub_title = {sa.wbs_prefix: sa for a in areas for sa in a.sub_areas}
    order = [normalize_name(g.title) for g in groups if normalize_name(g.title) in {normalize_name(a.title) for a in areas}]
    if order != [normalize_name(a.title) for a in areas if normalize_name(a.title) in order]:
        out.append("Product Scope lists its groups in a different order from the WBS areas.")
    for a in areas:
        g = by_title.get(normalize_name(a.title))
        if g is None:
            out.append(f"WBS area {a.number} {a.title!r} has no Product Scope group of the same name.")
            continue
        tagged = {it.wbs: it for it in g.items if it.wbs}
        for sa in a.sub_areas:
            it = tagged.get(sa.wbs_prefix)
            if it is None:
                out.append(f"Sub-area {sa.wbs_prefix} {sa.title!r} has no item in the {g.title!r} scope group ending (WBS {sa.wbs_prefix}).")
                continue
            done = bool(sa.features) and all(f.status in _SHIPPED for f in sa.features)
            if it.complete and not done:
                out.append(f"Scope item for {sa.wbs_prefix} is ticked as shipped but the sub-area still has unfinished features.")
            elif done and not it.complete:
                out.append(f"Every feature in {sa.wbs_prefix} has shipped: tick its scope item.")
        for it in g.items:
            if it.wbs is None:
                out.append(f"In {g.title!r}, an item has no (WBS x.y) tag: {it.text[:60]!r}")
            elif it.wbs not in sub_title:
                out.append(f"In {g.title!r}, an item points at {it.wbs}, which is not a sub-area.")
            elif not it.wbs.startswith(f"{a.number}."):
                out.append(f"In {g.title!r}, the item for {it.wbs} belongs under another area.")
    area_titles = {normalize_name(a.title) for a in areas}
    for g in groups:
        key = normalize_name(g.title)
        if key not in area_titles and key not in EXTRA_GROUPS:
            out.append(f"Scope group {g.title!r} is not a WBS area. Rename it to one, or add it as an area on the Structure page.")
    return out
