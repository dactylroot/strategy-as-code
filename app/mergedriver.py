"""Field-level three-way merge for PRODUCT.MD and BUGS.MD (git merge driver).

Standalone on purpose (standard library only, no package imports) so git can run
it as `python /path/to/mergedriver.py %O %A %B %P` from any repository.

How it works: every table row that has a key (a WBS code in PRODUCT.MD, a bug id
in BUGS.MD) is replaced by a placeholder line, and the remaining text (headings,
prose, table headers, row order) is merged by `git merge-file`. Each row is then
merged on its own, field by field:

  * a side that left a field as it was in the base loses to the side that changed it;
  * both sides changed it: Status takes the more advanced stage
    (Released > Live > In-Progress > Idea; Resolved > Investigating > Open),
    UAT Confirmed takes Yes, Notes keeps both, anything else keeps ours;
  * a bug that is Closed on either side stays Closed (the Closed row wins);
  * both sides adding the same key with different content keeps both: the
    incoming one is renumbered to the next free id.

Exit status 0 means a clean merge was written to the %A file; 1 leaves %A alone
so git reports an ordinary conflict.
"""
from __future__ import annotations
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PLACE = "@@strategy-row@@ "
FEATURE_RANK = {"idea": 0, "gap": 0, "scored": 0, "scoped": 0, "planned": 1, "in-progress": 1, "live": 2, "released": 3}
BUG_RANK = {"open": 0, "investigating": 1, "fix in progress": 2, "resolved": 2}


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _row_line(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _key_of(line: str, kind: str) -> str | None:
    if not line.lstrip().startswith("|"):
        return None
    first = _cells(line)[0]
    pat = r"\d+(?:\.\d+)+" if kind == "product" else r"\d+"
    return first if re.fullmatch(pat, first) else None


def _split(text: str, kind: str):
    """Return (skeleton lines, {key: [(section, cells)]})."""
    skeleton, rows, section = [], {}, ""
    for line in text.split("\n"):
        if line.startswith("## "):
            section = line[3:].strip().lower()
        k = _key_of(line, kind)
        if k is None:
            skeleton.append(line)
            continue
        skeleton.append(PLACE + k)
        rows.setdefault(k, []).append((section, _cells(line)))
    return skeleton, rows


def _headers(text: str, section: str | None, kind: str) -> list[str]:
    """Lower-cased column names of the table under `section` (or the nearest one)."""
    cur = ""
    for line in text.split("\n"):
        if line.startswith("## "):
            cur = line[3:].strip().lower()
        if line.lstrip().startswith("|") and _key_of(line, kind) is None and set(line) - set("|- :"):
            if section is None or cur == section:
                return [c.lower() for c in _cells(line)]
    return []


def _rank(value: str, table: dict) -> int:
    return table.get(value.strip().lower(), -1)


def _merge_field(name: str, b: str | None, o: str, t: str, kind: str) -> str:
    if o == t:
        return o
    if b is not None:
        if o == b:
            return t
        if t == b:
            return o
    if name == "status":
        table = FEATURE_RANK if kind == "product" else BUG_RANK
        return t if _rank(t, table) > _rank(o, table) else o
    if name.startswith("uat"):
        return o if o.lower() in ("yes", "true") else (t if t.lower() in ("yes", "true") else o)
    if name == "notes":
        return o if not t or t in o else (t if not o or o in t else f"{o} / {t}")
    return o


def _merge_cells(names: list[str], base, ours, theirs, kind: str) -> list[str]:
    n = max(len(ours), len(theirs), len(base or []))
    pad = lambda c: (c or []) + [""] * (n - len(c or []))
    b, o, t = (pad(base) if base is not None else None), pad(ours), pad(theirs)
    out = []
    for i in range(n):
        name = names[i] if i < len(names) else ""
        out.append(_merge_field(name, b[i] if b else None, o[i], t[i], kind))
    while out and not out[-1] and len(out) > len(ours):
        out.pop()                                   # don't grow the row with empty cells
    return out


def _pick(entries, section=None):
    """The (section, cells) entry for a given section, else the last one."""
    if not entries:
        return None
    for e in entries:
        if e[0] == section:
            return e
    return entries[-1]


def _renumber_collisions(base_rows, ours_rows, theirs_rows, theirs_skel, kind):
    """Rename incoming keys that the other side also added with different content."""
    taken = set(ours_rows) | set(theirs_rows) | set(base_rows)
    for key in sorted(theirs_rows):
        if key in base_rows or key not in ours_rows:
            continue
        if _identity(ours_rows[key], kind) == _identity(theirs_rows[key], kind):
            continue
        new = _next_key(key, taken, kind)
        taken.add(new)
        theirs_rows[new] = [(s, [new] + c[1:]) for s, c in theirs_rows.pop(key)]
        for i, l in enumerate(theirs_skel):
            if l == PLACE + key:
                theirs_skel[i] = PLACE + new
    return theirs_skel


def _identity(entries, kind):
    cells = entries[-1][1]
    return cells[1] if len(cells) > 1 else ""


def _next_key(key: str, taken: set[str], kind: str) -> str:
    if kind == "bugs":
        return str(max(int(k) for k in taken) + 1)
    prefix = key.rsplit(".", 1)[0]
    nums = [int(k.rsplit(".", 1)[1]) for k in taken if k.rsplit(".", 1)[0] == prefix]
    return f"{prefix}.{max(nums) + 1}"


def _merge_skeleton(base, ours, theirs) -> list[str] | None:
    """Three-way merge of the row-free text. A conflict made only of row
    placeholders (both sides added rows at the same spot) keeps both; any other
    conflict makes the whole merge fail."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, lines in (("o", ours), ("b", base), ("t", theirs)):
            p = Path(d) / name
            p.write_text("\n".join(lines), encoding="utf-8")
            paths.append(str(p))
        r = subprocess.run(["git", "merge-file", "-p", *paths], capture_output=True, text=True)
        if r.returncode < 0 or r.returncode > 127:
            return None
        lines = r.stdout.split("\n")
    if r.returncode == 0:
        return lines
    out, i = [], 0
    while i < len(lines):
        if not lines[i].startswith("<<<<<<< "):
            out.append(lines[i])
            i += 1
            continue
        mine, theirs_, cur, i = [], [], None, i + 1
        cur = mine
        while i < len(lines) and not lines[i].startswith(">>>>>>> "):
            if lines[i].startswith("======="):
                cur = theirs_
            else:
                cur.append(lines[i])
            i += 1
        i += 1
        if any(l and not l.startswith(PLACE) for l in mine + theirs_):
            return None
        out.extend(mine + [l for l in theirs_ if l not in mine])
    return out


def _restore_edited_rows(base_skel, side_skel, b_rows, own_rows, other_rows):
    """A side that deleted a row the other side edited gets the row back at its base position."""
    for key, b_entries in b_rows.items():
        if key in own_rows or key not in other_rows or other_rows[key] == b_entries:
            continue
        marker = PLACE + key
        at = base_skel.index(marker)
        prev = next((l for l in reversed(base_skel[:at]) if l in side_skel), None)
        pos = side_skel.index(prev) + 1 if prev is not None else 0
        side_skel.insert(pos, marker)
        own_rows[key] = list(b_entries)               # the deleter's version is the base row


def merge_text(base: str, ours: str, theirs: str, kind: str) -> str | None:
    """Merged text, or None when the prose/structure itself conflicts."""
    b_skel, b_rows = _split(base, kind)
    o_skel, o_rows = _split(ours, kind)
    t_skel, t_rows = _split(theirs, kind)
    t_skel = _renumber_collisions(b_rows, o_rows, t_rows, t_skel, kind)
    _restore_edited_rows(b_skel, o_skel, b_rows, o_rows, t_rows)
    _restore_edited_rows(b_skel, t_skel, b_rows, t_rows, o_rows)
    merged = _merge_skeleton(b_skel, o_skel, t_skel)
    if merged is None:
        return None
    out, section, seen = [], "", set()
    for line in merged:
        if line.startswith("## "):
            section = line[3:].strip().lower()
        if not line.startswith(PLACE):
            out.append(line)
            continue
        key = line[len(PLACE):]
        b, o, t = b_rows.get(key), o_rows.get(key), t_rows.get(key)
        if kind == "bugs":
            # a Closed row on either side wins; render in the section the line landed in
            for e_list in (o, t):
                for s, c in e_list or []:
                    if s in ("closed", "resolved"):
                        section = s
                        break
        o_e, t_e = _pick(o, section), _pick(t, section)
        if o_e is None or t_e is None:
            # present on one side only (an add, or the other side deleted it unchanged)
            e = o_e or t_e
            if e is None:
                continue
            if (o is None) != (t is None) and b is not None and (o or t)[-1][1] == b[-1][1]:
                continue                                  # deleted on the other side, untouched here
            out.append(_row_line(e[1]))
            continue
        if kind == "bugs" and o_e[0] != t_e[0]:
            # same bug in different sections (closed vs active): keep the closed one
            keep = o_e if o_e[0] in ("closed", "resolved") else t_e
            out.append(_row_line(keep[1]))
            continue
        b_e = _pick(b, section)
        names = _headers(ours, o_e[0], kind) or _headers(theirs, t_e[0], kind)
        cells = _merge_cells(names, b_e[1] if b_e else None, o_e[1], t_e[1], kind)
        out.append(_row_line(cells))
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) < 5:
        print("usage: mergedriver.py BASE OURS THEIRS PATH", file=sys.stderr)
        return 2
    base_p, ours_p, theirs_p, path = argv[1:5]
    kind = "bugs" if Path(path).name.upper().startswith("BUGS") else "product"
    read = lambda p: Path(p).read_text(encoding="utf-8")
    result = merge_text(read(base_p), read(ours_p), read(theirs_p), kind)
    if result is None:
        return 1
    Path(ours_p).write_text(result, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
