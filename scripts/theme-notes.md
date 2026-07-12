# Theme Reference Notes (strategy-as-code)

Source of truth: `app/templates/base.html`'s inline `<style>` block. This app
is FastAPI + Jinja2 + htmx with no build step and no Tailwind/MUI - theming
means plain CSS custom properties defined once in `base.html` (`:root { ... }`
for dark, `:root[data-theme="light"] { ... }` as the override block) and
consumed via `var(--...)` everywhere else, including in the page templates
under `app/templates/` and their partials.

This theme is a **hybrid of two sibling apps' design systems**, not a
one-to-one port of either:
- **solution-analytics** (`~/Workspace/solution-analytics`) supplied the
  brand identity: t3-blue accent, the teal/purple/amber/red categorical
  hues, Space Grotesk + JetBrains Mono typography, and the ambient blue
  grid-glow background (`AnimatedBackground.tsx` → `.grid-bg` here).
- **renewals-web-react** (`~/Workspace/renewals/web-react`) supplied the
  actual dark-mode *surface* palette and sidebar treatment: its MUI
  `theme/index.ts` navy tokens replaced solution-analytics' near-black
  glassmorphism surfaces, and its `AppShell.tsx` Drawer's solid-panel /
  right-border-active-item pattern replaced the original translucent
  left-border sidebar.

If asked to "match renewals" or "match solution-analytics" further, re-check
each source directly rather than assuming this doc stays in sync - it's a
snapshot, and either upstream app's own files may have moved on.

## Dark Mode Theme (default - no `data-theme` attribute needed)

### Base surfaces (renewals-web-react navy scale)
Three-tier elevation, taken directly from renewals' `theme/index.ts` consts
(`NAVY`, `NAVY_SURFACE`, `NAVY_ELEVATED`, `NAVY_BORDER`) rather than
solution-analytics' near-black + translucent-white-overlay approach:
- Page background: `#0d1b2a` (renewals' `NAVY`)
- Table-header / secondary-panel surface: `#132337` (renewals' `NAVY_SURFACE`)
  - also used as the sidebar background (see below)
- Card surface: `#1a2e45` (renewals' `NAVY_ELEVATED`), hover `#22374d`
  (a lightened step above it - renewals itself has no explicit hover hex;
  MUI's default `action.hover` overlay was approximated as a flat tone here)
- Border, subtle: `rgba(30,58,95,0.55)` (softened `NAVY_BORDER`)
- Border, muted: `#1e3a5f` (renewals' `NAVY_BORDER`, solid)
- Cards are **solid panels, not glass** - `--card-blur: none`, `--card-shadow: none`
  in dark mode (matches renewals' `MuiCard`: "no box-shadow in dark mode",
  no backdrop blur). This is a deliberate departure from solution-analytics'
  glassmorphism (`backdrop-filter: blur(16px)` translucent cards).

### Sidebar (renewals-web-react `AppShell.tsx` Drawer, adapted to a static left rail)
- Background: `#132337` (same `NAVY_SURFACE` tone as the table-header surface),
  solid - no `backdrop-filter` blur (renewals' Drawer paper isn't glass either)
- Border (right edge): `#1e3a5f` (`NAVY_BORDER`)
- Active nav item: **right**-side 3px accent bar + tinted background - mirrors
  renewals' `ListItemButton` `&.Mui-selected` (`borderRight: '3px solid',
  borderColor: 'primary.main'`, `bgcolor: 'rgba(0,188,212,0.12)'`), but using
  this app's own t3-blue accent instead of renewals' teal (`#00bcd4`), since
  t3-blue is the shared brand color across this app's own dataviz/badges -
  renewals' teal was not carried over as a competing accent.
  - Active background: `var(--color-t3-blue-dim)` = `rgba(0,163,255,0.15)`
  - Active border/text: `var(--color-t3-blue)` = `#00a3ff`
- Hover background: `rgba(0,163,255,0.08)` (this app's blue, same formula
  renewals uses for its own accent-tinted hover states)
- Section labels ("Planning", "Reference", ...): uppercase, small,
  letter-spaced, `rgba(255,255,255,0.35)` - renewals equivalent is its
  `ListSubheader` styling (`text.secondary`, no background)
- No collapse/expand behavior ported (renewals' Drawer is collapsible via a
  `DRAWER_WIDTH`/`DRAWER_COLLAPSED` toggle in its AppBar) - this app's
  sidebar is fixed-width only, as of this writing.

### Text
- Primary: `#e8f4fd` (renewals' `palette.text.primary` dark value)
- Secondary: `#90caf9` (renewals' `palette.text.secondary` dark value - a
  light blue, not solution-analytics' original slate-gray `#94a3b8`)
- Muted (chevrons, empty/loading states): `#4a5568` (solution-analytics'
  original value - renewals has no third "muted" text tier to borrow from)

### Brand / categorical palette (solution-analytics identity, unchanged)
| Var | Hex | Role |
|-----|-----|------|
| `--color-t3-blue` | `#00a3ff` | primary accent, links, active nav, "planned" status |
| `--color-teal` | `#00c4b4` | "good"/"live" status |
| `--color-amber` | `#f59e0b` | warning / idea / in-progress lifecycle stage |
| `--color-red` | `#ef4444` | critical / "gap" status |
| `--color-purple` | `#8b5cf6` | structural-hint / scoped / scored lifecycle stage |
| `--color-green` | `#76b900` | reserved, not actively used in this app's UI |

Status semantics (`--live`/`--planned`/`--gap`, used for feature/bug status
badges, stat values, progress bars) reuse these hues 1:1 - teal=live,
t3-blue=planned, red=gap - each with a `-bg` (≈0.12 alpha tint) and `-border`
(≈0.35 alpha) companion variable for badge fills. Amber/purple get the same
`-bg`/`-border`/`-text` treatment for the feature-lifecycle badges (idea,
gap, in-progress, scoped, scored partials under `app/templates/partials/`).

### Ambient background (solution-analytics `AnimatedBackground.tsx`)
A fixed, full-viewport `.grid-bg` div (first child of `<body>`, `z-index: -1`)
renders a faint blue grid (`rgba(0,163,255,0.05)` gridlines, 60px cells) with
a radial blue glow at the top (`rgba(0,163,255,0.08)`), masked to fade out
toward the bottom. This is what gives the dark theme its "ops-center blue"
feel on top of the navy surfaces above - without it the navy base alone reads
much flatter/darker. Present in both themes (very faint on the light
background), matching the source component's unconditional rendering.

### Typography
- Display/UI font: `'Space Grotesk'` (headings, nav, body) - Google Fonts
- Mono font: `'JetBrains Mono'` (badges, stat values, WBS codes) - Google Fonts

### Component style
- Cards/stat-cards: solid `#1a2e45`, `1px solid rgba(30,58,95,0.55)` border,
  no blur, no shadow (see "Base surfaces" above)
- Buttons: `.btn-primary` filled gradient `#0088d4 → #00a3ff` (unchanged from
  solution-analytics - reads fine on the new navy surfaces with no change
  needed); `.btn-ghost` transparent with muted border, text secondary → t3-blue
  on hover
- Table header (`th`): `#132337` background (the `NAVY_SURFACE` tier),
  matching renewals' `MuiTableCell` head styling
- Badges: pill-shaped, `<status>-bg` fill + `<status>-border` border,
  `<status>` text color
- Scrollbar: dark track, t3-blue-tinted thumb

## Light Mode Theme (`:root[data-theme="light"]`)

Unchanged from the original solution-analytics light-mode design (the
renewals-derived surface changes above are dark-mode-only, per how they were
requested) - re-tuned brand hues for contrast on a light surface, glass-card
blur dropped in favor of a flat white + soft shadow.

- Background: `#f7f8fa`; surface (table header): `#eef2f7`; card: `#ffffff`
  + `rgba(15,20,30,0.08)` border + `box-shadow: 0 1px 3px rgba(15,20,30,0.06)`
- Card hover: `#ffffff` + `rgba(0,163,255,0.04)` tint
- Text: primary `#0f172a`, secondary `#475569`, muted `#7c8797`
- Sidebar: `rgba(247,248,250,0.9)` translucent + blur (kept as originally
  designed for light mode - the renewals-solid-panel change only applied to
  dark mode)
- Brand hues darkened for AA contrast on white (t3-blue `#0088d4`, teal
  `#00897b`, amber `#d97706`; red/purple unchanged) with separate
  "text-safe" shades for standalone colored text vs. icon/badge marks (see
  `--live`/`--planned`/`--gap`/`--color-amber-text`/`--color-purple-text` in
  `base.html` - these ARE the text-safe values, marks are the raw brand hues)

## Full CSS variable reference (`base.html`, current)

```
--color-bg-base:        #0d1b2a   (light: #f7f8fa)
--color-bg-surface:     #132337   (light: #eef2f7)
--color-bg-card:        #1a2e45   (light: #ffffff)
--color-bg-card-hover:  #22374d   (light: rgba(0,163,255,0.04))
--color-border-subtle:  rgba(30,58,95,0.55)   (light: rgba(15,20,30,0.08))
--color-border-muted:   #1e3a5f               (light: rgba(15,20,30,0.14))
--card-blur:            none      (both modes)
--card-shadow:          none      (light: 0 1px 3px rgba(15,20,30,0.06))

--color-text-primary:   #e8f4fd   (light: #0f172a)
--color-text-secondary: #90caf9   (light: #475569)
--color-text-muted:     #4a5568   (light: #7c8797)

--color-t3-blue:        #00a3ff   (light: #0088d4)
--color-t3-blue-dim:    rgba(0,163,255,0.15)   (light: rgba(0,136,212,0.12))
--color-t3-blue-border: rgba(0,163,255,0.3)    (light: rgba(0,136,212,0.3))
--color-teal:           #00c4b4   (light: #00897b)
--color-purple:         #8b5cf6   (both modes)
--color-amber:          #f59e0b   (light: #d97706)
--color-red:             #ef4444  (both modes)
--color-green:          #76b900   (both modes)

--live / --live-bg / --live-border           (teal-derived, "good"/"live" status)
--planned / --planned-bg / --planned-border  (t3-blue-derived, "planned" status)
--gap / --gap-bg / --gap-border              (red-derived, "gap"/critical status)
--color-amber-text / -bg / -border           (warning / idea-lifecycle marks)
--color-purple-text / -bg / -border          (hint / scoped-lifecycle marks)

--sidebar-bg:            #132337  (light: rgba(247,248,250,0.9))
--sidebar-border:        #1e3a5f  (light: rgba(15,20,30,0.08))
--sidebar-text:          rgba(255,255,255,0.65)   (light: #475569)
--sidebar-text-active:   #f0f4ff                  (light: #0f172a)
--sidebar-hover-bg:      rgba(0,163,255,0.08)      (light: rgba(0,136,212,0.06))
--sidebar-section-color: rgba(255,255,255,0.35)    (light: #7c8797)

--sidebar-width: 14rem
--radius: 10px
--font-display: 'Space Grotesk', system-ui, -apple-system, sans-serif
--font-mono: 'JetBrains Mono', ui-monospace, monospace
```

## Implementation status

Fully wired up (unlike the original solution-analytics doc this file was
seeded from, which described an *unimplemented* proposal - that's no longer
true here):

- A blocking inline script in `<head>` (before the `<style>` block) reads
  `localStorage['strategy-as-code-theme']` and sets `data-theme="light"` on
  `<html>` before first paint, avoiding a flash of the wrong theme.
- A sun/moon toggle button lives in the sidebar footer (`#theme-toggle`),
  hidden whenever the server-side `embed_chrome` setting is true (the host
  is expected to control theme in that case - see below).
- **Embedded-mode theme inheritance**: when `embed_chrome` is set, this app
  skips localStorage entirely and instead listens for
  `postMessage({source: 'renewals-host', type: 'theme', mode: 'light'|'dark'})`
  (origin-checked against `window.location.origin`). This exact envelope and
  source string were taken directly from renewals-web-react's
  `SolutionAnalyticsAdminPage.tsx`, which broadcasts its own theme into the
  solution-analytics iframe on load and on every toggle - this app follows
  the same contract so it can be embedded the same way. As of this writing,
  renewals' `ProjectPlanningPage.tsx` (which iframes *this* app) does not yet
  send that message - the listener is ready, but the host side isn't wired up.
