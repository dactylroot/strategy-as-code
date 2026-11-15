"""Retire a WBS sub-area, or move some of its features, across the planning files.

PRODUCT.MD holds the WBS; ABOUT.MD initiatives and BUGS.MD active bugs cite feature codes. A move gives
features new codes, so the three files change together. This is pure text-in / text-out: the CLI applies
it to a checkout, the API to a session or to disk, and `dry_run` shows the outcome without writing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parsers import about as about_parser
from .parsers import bugs as bugs_parser
from .parsers import product as product_parser


@dataclass
class MoveResult:
    product: str
    about: str
    bugs: str
    mapping: dict[str, str]                      # old feature code -> new feature code
    removed_sub_area: str | None = None
    scope_item: str = "none"                     # none | dropped | folded
    initiatives_updated: int = 0
    bugs_updated: int = 0
    notes: list[str] = field(default_factory=list)

    def report(self) -> dict:
        return {"mapping": self.mapping, "removed_sub_area": self.removed_sub_area, "scope_item": self.scope_item,
                "initiatives_updated": self.initiatives_updated, "bugs_updated": self.bugs_updated,
                "notes": self.notes}


def _mentions(text: str, code: str) -> int:
    return len(re.findall(rf"(?<![\w.]){re.escape(code)}(?![\w.])", text))


def _rewrite_order(mapping: dict[str, str]) -> list[tuple[str, str]]:
    """Order the rewrites so none is applied on top of another's output. A new code can equal another
    feature's old code (1.1.1 -> 1.1.2 while 1.1.2 -> 1.1.3), so the entry that vacates a code goes
    before the one that takes it."""
    pending, out = dict(mapping), []
    while pending:
        ready = next((o for o, n in pending.items() if n not in pending), None)
        if ready is None:                       # a cycle cannot arise from a move or a renumber; keep the rest as given
            out.extend(pending.items())
            break
        out.append((ready, pending.pop(ready)))
    return out


def _follow(result: MoveResult, about: str, bugs: str) -> None:
    for old, new in _rewrite_order(result.mapping):
        a2 = about_parser.transform_rewrite_wbs_refs(about, old, new)
        result.initiatives_updated += a2 != about
        about = a2
        b2 = bugs_parser.transform_rewrite_wbs_refs(bugs, old, new)
        result.bugs_updated += b2 != bugs
        bugs = b2
    result.about, result.bugs = about, bugs
    left = sum(_mentions(result.product, old) for old in result.mapping)
    if left:
        result.notes.append(f"{left} other mention(s) of the old codes remain in PRODUCT.MD (notes, Known Gaps); review them")
    result.notes.append("initiatives in other projects or the manifest that cite the old codes are not updated")


def _sync_scope(result: MoveResult, prefixes: list[str]) -> None:
    """A sub-area that gained unfinished features is no longer shipped, and one left with only shipped
    features now is: keep the Product Scope tick in step so `structure check` stays clean."""
    for prefix in prefixes:
        before = product_parser.scope_item_complete(result.product, prefix)
        result.product = product_parser.transform_sync_scope_item(result.product, prefix)
        after = product_parser.scope_item_complete(result.product, prefix)
        if before is not None and before != after:
            result.notes.append(f"Product Scope item for {prefix} {'ticked: all of it has shipped' if after else 'unticked: it now holds unfinished features'}")


def _close_gap(result: MoveResult, area: int) -> None:
    """After a sub-area is removed, renumber the ones after it down so the area stays 1..n (the check
    insists on it). Their features, scope tags and references follow."""
    subs = [s["prefix"] for a in product_parser.structure_of(result.product) if a["number"] == area for s in a["sub_areas"]]
    for want, prefix in enumerate(sorted(subs, key=lambda p: int(p.split(".")[1])), 1):
        new = f"{area}.{want}"
        if prefix == new:
            continue
        result.product, codes = product_parser.transform_renumber_sub_area(result.product, prefix, new)
        for code in codes:
            fresh = new + code[len(prefix):]
            hit = [k for k, v in result.mapping.items() if v == code]     # a feature merged in earlier, now renumbered
            if hit:
                result.mapping[hit[0]] = fresh
            else:
                result.mapping[code] = fresh
        result.notes.append(f"sub-area {prefix} renumbered to {new} to close the gap")


def move_features(product: str, about: str, bugs: str, wbs_list: list[str], target: str) -> MoveResult:
    new_product, mapping = product_parser.transform_move_features(product, wbs_list, target)
    result = MoveResult(new_product, about, bugs, mapping)
    _sync_scope(result, sorted({c.rsplit(".", 1)[0] for c in list(mapping) + list(mapping.values())}))
    _follow(result, about, bugs)
    return result


def merge_sub_area(product: str, about: str, bugs: str, source: str, target: str, *,
                   fold_scope: bool = False, close_gap: bool = True) -> MoveResult:
    had_item = bool(re.search(rf"\(WBS {re.escape(source)}\)", product))
    new_product, mapping = product_parser.transform_merge_sub_area(product, source, target, fold_scope=fold_scope)
    result = MoveResult(new_product, about, bugs, mapping, removed_sub_area=source)
    if had_item:
        result.scope_item = "folded" if fold_scope else "dropped"
    _sync_scope(result, [target])
    if close_gap:
        _close_gap(result, int(source.split(".")[0]))
    _follow(result, about, bugs)
    return result


def merge_area(product: str, about: str, bugs: str, number: int, target: str, *,
               fold_scope: bool = False) -> MoveResult:
    """Retire a whole area: merge each of its sub-areas into `target`, then remove the empty area."""
    areas = product_parser.structure_of(product)
    area = next((a for a in areas if a["number"] == number), None)
    if area is None:
        raise ValueError(f"Area {number} not found")
    subs = [s["prefix"] for s in area["sub_areas"]]
    if target.split(".")[0] == str(number):
        raise ValueError(f"Target {target} is inside area {number}; pick a sub-area in another area.")
    total = MoveResult(product, about, bugs, {})
    for prefix in subs:
        step = merge_sub_area(total.product, total.about, total.bugs, prefix, target, fold_scope=fold_scope, close_gap=False)
        total.product, total.about, total.bugs = step.product, step.about, step.bugs
        total.mapping.update(step.mapping)
        total.initiatives_updated += step.initiatives_updated
        total.bugs_updated += step.bugs_updated
        if step.scope_item != "none":
            total.scope_item = step.scope_item
        total.notes.extend(n for n in step.notes if n not in total.notes)
    remaining = [a for a in product_parser.structure_of(total.product) if a["number"] != number]
    total.product = product_parser.transform_structure(total.product, remaining)
    total.removed_sub_area = f"area {number}"
    _drop_empty_scope_group(total, area["title"])
    numbers = [a["number"] for a in remaining]
    if numbers != list(range(1, len(numbers) + 1)):
        total.notes.append("the remaining areas are no longer numbered 1 to N; codes never change, so "
                           "`strategy structure check` will keep saying so")
    return total


def _drop_empty_scope_group(result: MoveResult, title: str) -> None:
    """The Product Scope group named for a retired area goes with it once no item is left in it."""
    sm = re.search(r"(^## Product Scope\n)(.*?)(?=\n## |\Z)", result.product, re.DOTALL | re.MULTILINE)
    if not sm:
        return
    gm = re.search(rf"^### {re.escape(title)}[ \t]*\n(.*?)(?=^### |\Z)", sm.group(2), re.DOTALL | re.MULTILINE)
    if not gm:
        return
    if re.search(r"^\s*-", gm.group(1), re.MULTILINE):
        result.notes.append(f"Product Scope group {title!r} still has items; move or remove them by hand")
        return
    body = sm.group(2)[:gm.start()] + sm.group(2)[gm.end():]
    result.product = result.product[:sm.start(2)] + body.rstrip("\n") + "\n" + result.product[sm.end(2):]
