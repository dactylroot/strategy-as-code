
---
  description: Use this skill when discussing project plans, receiving new requirements, after implementing new release-level functionality, or when requested to update documentation
---

# SKILL: Program Strategy Manager

## Purpose
Manage product roadmaps, write PRDs, prioritize features, and track releases using four plain-text markdown files as the long-term store. All product state lives in these files - no external tools required.

## Capabilities
- Generate product roadmaps from vision statements
- Write PRDs and user stories from feature descriptions
- Score and prioritize features using Value/Effort or custom frameworks
- Draft release notes and changelog entries
- Track goals and OKRs against initiatives
- Create competitive analysis summaries
- Generate capacity planning estimates
- Synthesize customer feedback into actionable feature requests

## Usage
Tell me your product vision, current backlog items, or strategic goals.
I will organize, prioritize, and document everything.

---

## Feature Lifecycle

All features live in PRODUCT.MD. There is no separate backlog file.

```
Gap / Idea → [Scoped] → [Scored] → In-Progress → Live → Released
```

`Scoped` and `Scored` are **derived, not stored** - never write them into the Status column. A Gap or Idea *displays* as Scoped once it has real Notes, and as Scored once Value and Effort are also both set. This is computed at read time from the row's own Notes/Value/Effort - there's nothing to keep in sync, which eliminates the drift that used to happen between the stored status and the actual score/notes data.

| Stage | Stored? | Meaning |
|-------|---------|---------|
| **Gap** | Yes | Identified need in the WBS - not yet defined or prioritized |
| **Idea** | Yes | Captured idea assigned to a WBS area - raw, unstructured |
| **Scoped** | Derived | Gap/Idea with non-empty Notes - defined and ready for scoring |
| **Scored** | Derived | Scoped, with Value & Effort also both set - ready to start work |
| **In-Progress** | Yes | Work has started |
| **Live** | Yes | Shipped and in production; awaiting UAT sign-off |
| **Released** | Yes | Approved after UAT; final state |

**All features require a WBS code.** When capturing a new idea, assign it to a WBS sub-area immediately. It gets a full WBS code (e.g. 1.3.4) and status `Idea`.

**Scoring uses Value and Effort (1–10 each).** Priority score = Value ÷ Effort. Writing Notes on a Gap/Idea feature is what makes it read as Scoped; adding both scores on top of that is what makes it read as Scored. Clearing the scores (or the Notes) reverts the display automatically - there is no separate status to set or revert.

**Status transitions actually written to the Status column:**
- Gap → Idea (captured to WBS area) → In-Progress (work started) → Live (shipped, awaiting UAT) → Released (UAT approved)

Scoped and Scored never appear as literal transitions - they just happen to be how a Gap/Idea row currently reads based on its Notes/Value/Effort.

**Review and UAT mirror the bug lifecycle.** A `Live` feature is done but not yet accepted, exactly like a `Resolved` bug; `Released` is the accepted final state, exactly like a `Closed` bug. The feature board shows this directly:

| Board column | Status | Meaning |
|--------------|--------|---------|
| **In Progress** | `In-Progress` | Work underway |
| **Review** | `Live` | Completed, awaiting UAT sign-off |
| **Released** | `Released` | Approved after UAT |

Marking a feature Complete sets it to `Live`, so it lands in **Review** and stays there until a human approves it - independent of any changelog or release entry. Every `Live` feature is in Review; approving one advances it to `Released`. Never set `Released` yourself from a code change - that transition is the human UAT step (see below).

**When implementation work finishes, advance the feature to `Live` immediately - do not wait to be asked.** If a code change (bug fix or new functionality) satisfies an `In-Progress` (or `Gap`/`Idea`) feature's described requirement, update PRODUCT.MD in the same pass as the code change:
- Move its `Status` to `Live`. Never set `Released` from a code change alone - that status requires a human UAT sign-off and is a separate, later, human-triggered step.
- Rewrite the `Notes` text if it was phrased as a pending requirement (e.g. "X should do Y"). Once shipped, describe the implemented behavior instead (e.g. "X does Y"), so a `Live` row doesn't read like an open TODO.
- If the feature has no existing WBS row, don't fabricate one - ask which existing feature it belongs to, or whether it needs a new row first.

**Regenerate the WBS chart after any status edit.** `docs/wbs.html`/`docs/wbs.png` are generated artifacts, not hand-maintained - a PRODUCT.MD status change alone leaves them stale. Re-run `gen_wbs.py` (see WBS Chart Generation below) as part of the same change.

---

## Internal Documents

### PRODUCT.MD

Stakeholder and leadership-facing product overview. The canonical registry for all features and their status.

**Section order:**

1. **Summary** - One paragraph describing what the product is and who it serves.
2. **Users** - One subsection per user persona. Each persona gets a short paragraph describing their role and what they do with the product.
3. **Product Scope** - Bulleted list of scope areas and their initiatives. Use strikethrough + 🎆 for completed items. Example:
   ```
   ### Core Functionality
   - ~~IBR Ingestion Automation and Data Centralization~~ 🎆
   - UIUX design to enable the LCR team to manage data mappings themselves
   ```
4. **WBS Chart** - Embed the chart as a text link to the interactive HTML + a Markdown image for the PNG:
   ```markdown
   [Interactive version](docs/wbs.html)

   ![Product Work Breakdown Structure](docs/wbs.png)
   ```
   Do NOT use `<iframe>` or `<style>` tags. GitHub strips them and outputs their content as raw text.
5. **Core Workflows** - Numbered subsection per user persona. Each workflow is a numbered step list showing the end-to-end flow.
6. **Features** - WBS-coded tables grouped by scope area and sub-area. Columns: `WBS | Feature | Status | Value | Effort | Notes`. See status values below.
7. **Known Gaps for Team Discussion** - One subsection per gap. Written as a short paragraph explaining the problem, what's missing, and why it matters. Gaps listed here should have a corresponding `Gap` status row in the Features table above.

**WBS code format:** `{scope}.{area}.{feature}` (e.g. 1.1.3, 2.2.1)

**Feature table format:**
```markdown
| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.1.1 | Feature name | Gap | | | Optional notes |
| 1.1.2 | Idea with real notes and both scores set | Idea | 8 | 3 | A real description, not just a bare title |
```
The second row above displays as **Scored** in the UI and skill output, even though the Status column literally says `Idea` - see Feature Lifecycle. Value and Effort columns are optional - leave both blank on a bare, undescribed idea.

**Strikethrough convention:** Use `~~text~~ 🎆` for completed Product Scope items. Apply at the scope initiative level, not at the individual feature level.

**Status values written to the Status column:**
- `Gap` - identified need; not yet scoped or prioritized
- `Idea` - captured to a WBS area; not yet fully defined
- `In-Progress` - work has started
- `Live` - shipped and in production; awaiting UAT sign-off
- `Released` - UAT approved; final state
- `Planned` - legacy alias for In-Progress (backward compatible; do not use for new features)

**`Scoped` and `Scored` are never written** - they're derived from a Gap/Idea row's Notes/Value/Effort at display time (see Feature Lifecycle above). If you encounter either literal text in an older file's Status column, treat it as `Idea` - the app normalizes it automatically on read.

### ABOUT.MD

User-facing changelog and high-level roadmap. Should be plaintext and friendly for embedding in the application "About" page.

**Versioning scheme: MAJOR.MINOR.RELEASE**
- MINOR increments each time a WBS Level 2 sub-area ships.
- RELEASE increments for builds and bug fixes within a minor version (e.g. hotfixes after 0.2.0 ships become 0.2.1, 0.2.2).
- MAJOR stays at 0 until all product scope is complete, then becomes 1.0.0.
- Version numbers are assigned at release time in completion order - do not pre-assign version numbers to WBS sub-areas, since milestones may ship out of order.

**Changelog format:** One entry per version. Group changes under their WBS sub-area label (e.g. `**1.2 Self-Service Mapping UX**`). Bug fixes get their own unlabeled section. Most recent version first.

Always place a blank line between a bold sub-area label and its list items. Python-Markdown requires this to render list items as `<li>` elements; without it, bullets appear as plain text in the About page.

**Roadmap format:**
```markdown
## In Progress
- {WBS sub-area label}

## Planned
### v0.5.0
- {WBS sub-area label}

### v0.6.0
- {WBS sub-area label}

## Backlog
- Items outside current product scope
```

The `## Planned` section uses `### version-label` sub-sections to group planned sub-areas by target release. Unassigned items go under a `### Unassigned` bucket. Omit the `### Unassigned` header if all planned items are bucketed.

Do not pre-assign version numbers in the roadmap. The WBS label is the identifier; the version number is only meaningful once the work ships.

### README.MD

Operational and development details including technical architecture, local setup, configuration reference, database migrations, testing, build/deploy, and authentication modes.

### BUGS.MD

Bug tracking. Two sections: `## Active` (bugs still on the board, including code-fixed ones awaiting UAT) and `## Closed` (bugs fully done and verified).

**Active table format:**
```markdown
## Active

| ID | Title | Severity | Status | Notes | WBS | Fix Version | Owner | UAT Confirmed | GH Issue |
|----|-------|----------|--------|-------|-----|-------------|-------|----------------|----------|
| 1 | Short title | Medium | Open | Optional notes | 1.2.3 |  |  |  |  |
```

**Severity values:** `Critical`, `High`, `Medium`, `Low`

**Status values:** `Open`, `Investigating`, `Resolved`

**Bug status lifecycle:**
- `Open` → `Investigating` (root cause being identified)
- `Investigating` → `Resolved` (code-level fix has landed, but not yet verified in UAT)
- `Resolved` → `Closed` (fix verified - user confirms or tests pass in production; the bug moves to the `## Closed` section)

`Open`, `Investigating`, and `Resolved` are all *active* - a bug keeps its board row and, when mirrored, its GitHub Issue stays open through all three. Only moving to `## Closed` closes the mirrored Issue. This matches the GitHub flow: `Open → Open → Open → Closed`.

**When a fix is implemented but not yet verified by the user or in production, set status to `Resolved`.** Do not close the bug (move it to `## Closed`) until the fix is confirmed in UAT.

**Fix Version** is the planned build version in which the fix will ship (e.g. `0.2.2`). Set it when advancing a bug to `Resolved`. Leave blank for `Open` and `Investigating` bugs. The UI displays it as a badge on Resolved cards.

**Owner** is a free-text name/handle of whoever is working the bug. Optional; leave blank if unassigned.

**UAT Confirmed** is `Yes` once a human has verified the fix in production/staging - it's the signal that a `Resolved` bug is ready to advance to `Closed`. Leave blank until then; any value other than `Yes`/`true` reads as unconfirmed.

**GH Issue** is a one-way mirror column - never hand-edit it. A background reconciliation loop creates a real GitHub Issue for any bug row that doesn't have one yet, writes the issue number back into this column, and keeps the Issue's open/closed state in sync with which section the row lives in (`## Active` → open, `## Closed` → closed). Each mirrored Issue's body embeds an invisible `<!-- strategy-as-code:bugs-md-id=N -->` marker so re-scans stay idempotent even if this column's value is ever lost (e.g. a deploy that restores BUGS.MD from a stale commit and reverts the column to blank - this has happened in practice). If a bug's Notes mention a screenshot being attached, the same loop looks for `.screenshots/bug_<ID>.*` in the project directory and uploads it as a GitHub Release asset linked from the mirrored Issue.

**Closed table format:**
```markdown
## Closed

| ID | Title | Resolved In | Date | GH Issue |
|----|-------|-------------|------|----------|
| 3 | Short title | 0.2.0 | 2025-04-10 |  |
```

When closing a bug, its row is removed from `## Active` and a new row is appended to `## Closed` with the version and date (its GH Issue number, if any, carries over). `Fix In Progress` on an old row still reads as `Resolved` (a retired status folded into the current lifecycle), and a legacy `## Resolved` header is still read as `## Closed` so old files keep working - but rename a legacy `## Resolved` header to `## Closed` the next time you touch that file, rather than perpetuating the old name.

The `WBS` column in Active is optional; leave blank if the bug has no feature association.

IDs are auto-assigned integers starting at 1. Do not reuse IDs.


---

## WBS Chart Generation

The chart generator script ships with the skill. Find it in order:

1. `~/.claude/skills/program-strategy/scripts/gen_wbs.py` (global install)
2. `.claude/skills/program-strategy/scripts/gen_wbs.py` relative to the project root (per-project install)

Run it from the project root:
```bash
python ~/.claude/skills/program-strategy/scripts/gen_wbs.py
```

Outputs:
- `docs/wbs.png` - static PNG (150 DPI, 30×12 in)
- `docs/wbs.html` - self-contained interactive HTML with hover effects

### Updating the chart

PRODUCT.MD is the single source of truth. The script parses the `## Features` section directly - no separate data structure to maintain. Edit PRODUCT.MD and re-run the script.

The parser reads:
- `### N. Title` → swimlane (Level 1 scope area)
- `#### N.N Title` → section (Level 2 sub-area)
- `| WBS | Feature | Status |` table rows → features (Level 3)

Status values in PRODUCT.MD: `Gap`, `Idea`, `In-Progress`, `Live`, `Released`, `Planned` (legacy). Legacy `Scoped`/`Scored` text from older files is normalized to `Idea`. The chart only distinguishes done (`Live`/`Released`, shown struck through) from everything else - it doesn't need the Scoped/Scored derivation, since Gap/Idea/In-Progress/Planned are all "not done" either way.

### Chart design notes

- Swimlane layout: one horizontal lane per Level 1 scope area
- Level 2 section widths are proportional to feature count, with minimum width enforced so short headers never clip (`distribute_widths()`)
- Section headers use a red-to-blue heatmap (0% complete = red, 100% = blue) via `LinearSegmentedColormap`
- Level 3 features are listed as text inside the section body; Live features render with strikethrough and muted color
- HTML version uses `flex` proportional widths and `column-count: 2` for sections with more than 9 features
- PNG dimensions: 22×12 in at 150 DPI; HTML capped at `max-width: 960px`

---

## Running the UI

When asked to run, open, start, or launch the UI, find the skill's launcher script and run it in the background with `PROJECT_DIR` set to the current working directory.

**Locate the script** - check in order until one exists:
1. `~/.claude/skills/program-strategy/scripts/run-ui.sh` (global install)
2. `.claude/skills/program-strategy/scripts/run-ui.sh` relative to the project root (per-project install)

**Run it:**
```bash
PROJECT_DIR=$(pwd) bash /path/to/run-ui.sh &
```

After starting, open the browser:
```bash
open http://localhost:8765
```

If the port is already in use, set `PORT` to a different value and pass it through:
```bash
PORT=8766 PROJECT_DIR=$(pwd) bash /path/to/run-ui.sh &
open http://localhost:8766
```

The UI reads the markdown files from `PROJECT_DIR` on each page load - no restart needed after editing files.

---

## External Documents

Generate on-demand when needed. If they lack details, capture those details in the internal documentation first:
Roadmap tables, PRD docs, Gantt-style timelines, OKR trackers, sprint plans

## Integrations

Works with GitHub Issues, Linear, Notion, Jira via paste-in context
