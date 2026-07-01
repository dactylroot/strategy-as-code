"""
gen_wbs.py - WBS swimlane chart generator for strategy-as-code.

Reads PROJECT_DIR env var to locate PRODUCT.MD and write output.
Outputs:
  $PROJECT_DIR/docs/wbs.png   - static PNG
  $PROJECT_DIR/docs/wbs.html  - self-contained interactive HTML
"""

import html as html_mod
import os
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import LinearSegmentedColormap
from math import ceil

# ── CONFIG ────────────────────────────────────────────────────────────────────
_BASE    = os.environ.get('PROJECT_DIR', '/project')
OUT      = os.path.join(_BASE, 'docs', 'wbs.png')
OUT_HTML = os.path.join(_BASE, 'docs', 'wbs.html')
PRODUCT_MD = os.path.join(_BASE, 'PRODUCT.MD')

os.makedirs(os.path.dirname(OUT), exist_ok=True)

FIG_W, FIG_H = 22, 12
DPI          = 150
BG           = '#f8fafc'

LABEL_X0, LABEL_X1     = 0.008, 0.098
CONTENT_X0, CONTENT_X1 = 0.101, 0.997
CONTENT_W = CONTENT_X1 - CONTENT_X0

TITLE_TOP    = 0.985
LANES_TOP    = 0.930
LANES_BOTTOM = 0.020
LANE_GAP     = 0.012

SEC_GAP      = 0.004
HEADER_FRAC  = 0.24

TITLE_FONT   = 20
LANE_FONT    = 13
HDR_LBL_FONT = 11
HDR_PCT_FONT = 13
FEAT_FONT    = 10.5
LINE_SPACING = 1.55

HDR_PAD_PX   = 100
HDR_MARGIN   = 0.007

LANE_LABEL_BG = '#1e293b'
EDGE_COLOR    = '#e2e8f0'

CMAP = LinearSegmentedColormap.from_list('RdBl', ['#b91c1c', '#1e40af'])

# ── DATA ──────────────────────────────────────────────────────────────────────

def make_lane_label(num, title):
    words = title.split()
    lines = [num]
    current = ''
    for word in words:
        if current and len(current) + 1 + len(word) > 12:
            lines.append(current)
            current = word
        else:
            current = (current + ' ' + word).strip()
    if current:
        lines.append(current)
    return '\n'.join(lines)

# Scoped/Scored are display-derived (see Feature.stage in app/models.py),
# never stored - normalize any legacy literal text from older files to Idea.
_LEGACY_STATUS = {'scoped': 'idea', 'scored': 'idea'}


def parse_product_md(path):
    with open(path, encoding='utf-8') as fh:
        text = fh.read()

    m = re.search(r'^## Features\n', text, re.MULTILINE)
    if not m:
        raise ValueError(f'## Features section not found in {path}')
    body = text[m.end():]
    stop = re.search(r'^## ', body, re.MULTILINE)
    if stop:
        body = body[:stop.start()]

    swimlanes, lane, section = [], None, None
    for line in body.splitlines():
        m1 = re.match(r'^### (\d+)\. (.+)$', line)
        if m1:
            if lane:
                swimlanes.append(lane)
            lane = {'label': make_lane_label(m1.group(1), m1.group(2).strip()), 'sections': []}
            section = None
            continue
        m2 = re.match(r'^#### (\d+\.\d+) (.+)$', line)
        if m2:
            section = {'label': f'{m2.group(1)}  {m2.group(2).strip()}', 'features': []}
            if lane:
                lane['sections'].append(section)
            continue
        m3 = re.match(r'^\| \S+ \| (.+?) \| (Gap|Idea|Scoped|Scored|In-Progress|Live|Released|Planned) \|', line)
        if m3 and section is not None:
            status = _LEGACY_STATUS.get(m3.group(2).lower(), m3.group(2).lower())
            section['features'].append({'label': m3.group(1).strip(), 'status': status})
    if lane:
        swimlanes.append(lane)
    return swimlanes

def get_product_title(path):
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            m = re.match(r'^# (.+)', line)
            if m:
                return m.group(1).strip()
    return 'Work Breakdown Structure'

SWIMLANES = parse_product_md(PRODUCT_MD)
PRODUCT_TITLE = get_product_title(PRODUCT_MD)
LANE_COUNT = len(SWIMLANES)
LANE_H = (LANES_TOP - LANES_BOTTOM - LANE_GAP * max(LANE_COUNT - 1, 0)) / max(LANE_COUNT, 1)

# ── HELPERS ──────────────────────────────────────────────────────────────────
DONE_STATUSES = ('live', 'released')


def completion(section):
    feats = section['features']
    if not feats:
        return 0.0
    return sum(1 for item in feats if item['status'] in DONE_STATUSES) / len(feats)

def body_tint(ratio):
    r, g, b, _ = CMAP(ratio)
    return (r * 0.15 + 0.85, g * 0.15 + 0.85, b * 0.15 + 0.85, 1.0)

def strike(text):
    return ''.join(c + '̶' for c in text)

def lane_bounds(i):
    y_top = LANES_TOP - i * (LANE_H + LANE_GAP)
    return y_top - LANE_H, y_top

def min_sec_width(label):
    char_w   = HDR_LBL_FONT * DPI / 72 * 0.72
    label_px = len(label) * char_w
    pct_px   = 4 * HDR_PCT_FONT * DPI / 72 * 0.72
    margin_px = 2 * HDR_MARGIN * FIG_W * DPI
    return (label_px + pct_px + HDR_PAD_PX + margin_px) / (FIG_W * DPI)

def distribute_widths(sections, avail_w):
    counts = [max(len(s['features']), 1) for s in sections]
    total  = sum(counts)
    widths = [avail_w * c / total for c in counts]
    mins   = [min_sec_width(s['label']) for s in sections]

    for _ in range(len(sections)):
        below = [i for i, (w, m) in enumerate(zip(widths, mins)) if w < m - 1e-9]
        if not below:
            break
        deficit = sum(mins[i] - widths[i] for i in below)
        above   = [i for i in range(len(sections)) if i not in below]
        surplus = sum(widths[i] - mins[i] for i in above)
        if surplus < 1e-9:
            break
        for i in below:
            widths[i] = mins[i]
        for i in above:
            widths[i] -= deficit * (widths[i] - mins[i]) / surplus

    return widths

# ── DRAW ──────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.axis('off')
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)

ax.text(0.5, TITLE_TOP,
        f'{PRODUCT_TITLE} - Work Breakdown Structure',
        ha='center', va='top', fontsize=TITLE_FONT,
        fontweight='bold', color='#0f172a')

for lane_i, lane in enumerate(SWIMLANES):
    y0, y1   = lane_bounds(lane_i)
    header_h = LANE_H * HEADER_FRAC
    body_h   = LANE_H - header_h

    avail_w  = CONTENT_W - SEC_GAP * (len(lane['sections']) - 1)
    widths   = distribute_widths(lane['sections'], avail_w)

    ax.add_patch(mpatches.FancyBboxPatch(
        (LABEL_X0, y0), LABEL_X1 - LABEL_X0, LANE_H,
        boxstyle='round,pad=0.004',
        facecolor=LANE_LABEL_BG, edgecolor=BG, linewidth=1.5, zorder=2))
    ax.text((LABEL_X0 + LABEL_X1) / 2, (y0 + y1) / 2,
            lane['label'], ha='center', va='center',
            fontsize=LANE_FONT, fontweight='bold', color='white',
            multialignment='center', linespacing=1.45, zorder=3)

    x_cursor = CONTENT_X0
    for sec, sec_w in zip(lane['sections'], widths):
        n     = len(sec['features'])
        ratio = completion(sec)
        sx0   = x_cursor

        ax.add_patch(mpatches.Rectangle(
            (sx0, y0), sec_w, LANE_H,
            facecolor=body_tint(ratio), edgecolor=EDGE_COLOR,
            linewidth=0.8, zorder=2))

        ax.add_patch(mpatches.Rectangle(
            (sx0, y1 - header_h), sec_w, header_h,
            facecolor=CMAP(ratio), edgecolor='none', zorder=3))

        ax.plot([sx0, sx0 + sec_w], [y1 - header_h, y1 - header_h],
                color='white', lw=0.6, zorder=4)

        ax.text(sx0 + HDR_MARGIN, y1 - header_h / 2,
                sec['label'], ha='left', va='center',
                fontsize=HDR_LBL_FONT, fontweight='bold',
                color='white', zorder=5)
        ax.text(sx0 + sec_w - HDR_MARGIN, y1 - header_h / 2,
                f'{ratio * 100:.0f}%', ha='right', va='center',
                fontsize=HDR_PCT_FONT, fontweight='bold',
                color='white', zorder=5)

        line_h_data  = (FEAT_FONT / 72) / FIG_H * LINE_SPACING
        max_rows     = max(1, int(body_h * 0.84 / line_h_data))
        n_cols       = max(1, ceil(n / max_rows))
        rows_per_col = ceil(n / n_cols) if n > 0 else 1
        col_w        = sec_w / n_cols
        body_cy      = y0 + body_h / 2

        for col_i in range(n_cols):
            start     = col_i * rows_per_col
            col_items = sec['features'][start : start + rows_per_col]
            n_rows    = len(col_items)
            col_cx    = sx0 + (col_i + 0.5) * col_w
            for row_i, item in enumerate(col_items):
                y     = body_cy + ((n_rows - 1) / 2 - row_i) * line_h_data
                live  = item['status'] in DONE_STATUSES
                label = strike(item['label']) if live else item['label']
                color = '#94a3b8' if live else '#1e293b'
                ax.text(col_cx, y, '• ' + label,
                        ha='center', va='center',
                        fontsize=FEAT_FONT, color=color, zorder=5)

        x_cursor += sec_w + SEC_GAP

plt.savefig(OUT, dpi=DPI, bbox_inches='tight', facecolor=BG)
print(f'Saved: {OUT}')

# ── HTML OUTPUT ───────────────────────────────────────────────────────────────
def gen_html(out_path):
    def css_rgb(ratio):
        r, g, b, _ = CMAP(ratio)
        return f'rgb({int(r*255)},{int(g*255)},{int(b*255)})'

    def css_tint(ratio):
        r, g, b, _ = CMAP(ratio)
        return f'rgb({int((r*.15+.85)*255)},{int((g*.15+.85)*255)},{int((b*.15+.85)*255)})'

    CSS = """
* { box-sizing: border-box; margin: 0; padding: 0 }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8fafc; padding: 16px; min-width: 680px; max-width: 960px; margin: 0 auto; }
.wbs-title { text-align: center; font-size: 1.25rem; font-weight: 700; color: #0f172a; margin-bottom: 14px; }
.lane { display: flex; margin-bottom: 6px; border-radius: 6px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
.lane-lbl { width: 86px; min-width: 86px; background: #1e293b; color: #fff; display: flex; align-items: center; justify-content: center; text-align: center; font-size: .78rem; font-weight: 700; padding: 8px; line-height: 1.45; }
.secs { display: flex; flex: 1; gap: 3px; background: #e2e8f0; padding: 3px; }
.sec { display: flex; flex-direction: column; border-radius: 4px; overflow: hidden; min-width: 0; transition: filter .15s, box-shadow .15s; cursor: default; }
.sec:hover { filter: brightness(.93); box-shadow: 0 0 0 2px rgba(255,255,255,.55) inset; }
.sec-hdr { display: flex; justify-content: space-between; align-items: flex-start; padding: 7px 9px; min-height: 44px; gap: 8px; }
.sec-lbl { font-size: .82rem; font-weight: 700; color: #fff; line-height: 1.35; flex: 1; }
.sec-pct { font-size: 1.0rem; font-weight: 700; color: #fff; white-space: nowrap; flex-shrink: 0; padding-top: 1px; }
.sec-body { flex: 1; padding: 8px 10px 10px; font-size: .82rem; color: #1e293b; line-height: 1.75; column-gap: 10px; }
.sec-body ul { list-style: none }
.sec-body li::before { content: "• "; color: #64748b }
.legend { display: flex; gap: 14px; margin-top: 10px; font-size: .78rem; color: #374151; flex-wrap: wrap; align-items: center; }
.ld { display: inline-flex; align-items: center; gap: 5px }
.ld-swatch { width: 28px; height: 10px; border-radius: 2px; flex-shrink: 0; }
"""

    lane_blocks = []
    for lane in SWIMLANES:
        sec_parts = []
        for sec in lane['sections']:
            n      = len(sec['features'])
            ratio  = completion(sec)
            n_cols = 2 if n > 9 else 1
            items  = ''.join(
                f'<li style="text-decoration:line-through;color:#94a3b8">{html_mod.escape(feat["label"])}</li>'
                if feat['status'] in DONE_STATUSES else
                f'<li>{html_mod.escape(feat["label"])}</li>'
                for feat in sec['features']
            )
            sec_parts.append(
                f'<div class="sec" style="flex:{max(n,1)};background:{css_tint(ratio)}">'
                f'<div class="sec-hdr" style="background:{css_rgb(ratio)}">'
                f'<span class="sec-lbl">{html_mod.escape(sec["label"])}</span>'
                f'<span class="sec-pct">{ratio*100:.0f}%</span>'
                f'</div>'
                f'<div class="sec-body" style="column-count:{n_cols}">'
                f'<ul>{items}</ul>'
                f'</div>'
                f'</div>'
            )
        lbl = lane['label'].replace('\n', '<br>')
        lane_blocks.append(
            f'<div class="lane">'
            f'<div class="lane-lbl">{lbl}</div>'
            f'<div class="secs">{"".join(sec_parts)}</div>'
            f'</div>'
        )

    grad_start = css_rgb(0.0)
    grad_end   = css_rgb(1.0)

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html_mod.escape(PRODUCT_TITLE)} - Work Breakdown Structure</title>
<style>{CSS}</style>
</head>
<body>
<p class="wbs-title">{html_mod.escape(PRODUCT_TITLE)} - Work Breakdown Structure</p>
{"".join(lane_blocks)}
<div class="legend">
  <span class="ld">
    <span class="ld-swatch" style="background:linear-gradient(to right,{grad_start},{grad_end})"></span>
    Section header: red = 0% complete &rarr; blue = 100% complete
  </span>
</div>
</body>
</html>"""

    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(html_out)
    print(f'Saved: {out_path}')

gen_html(OUT_HTML)
