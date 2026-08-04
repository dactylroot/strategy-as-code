# Changelog

## 0.6.0 (in progress)

**1.1 Dashboard**
- Feature Registry and Flagged Gaps consolidated onto the Summary page; the standalone Registry page is gone (`/registry` and `/product` redirect to `/dashboard`)
- Feature Lifecycle Pipeline stat row (Ideas / Prioritized / In Progress / Review / Released) replaces the old in-progress features list
- Recent Releases panel groups the last 3 versions' entries instead of showing a flat last-3-entries list

**1.2 Features Board**
- Feature deletion with strict name-confirmation dialog
- Feature search query preserved across page reloads via sessionStorage
- → In Progress action visible on Idea and Scored cards and in feature panel
- Idea now advances to Scored on Value alone (Effort defaults to 5 for priority scoring if left blank); Notes no longer promote a feature out of Ideas, and the derived Scoped stage is removed
- Name/Notes auto-save on blur like every other panel field; the separate Save button is gone
- A feature's gap flag auto-clears when it ships (Live) or is released, unless the same request explicitly re-flags it
- Replaced all browser confirm()/alert() popups with a themed in-app dialog matching the rest of the UI

**1.3 Roadmap**
- Replaced pre-planned version buckets with named, renamable Initiatives tagged Major or Minor via a clickable toggle; completing every feature in an Initiative drives the next release's semver bump
- Removed the old In Progress column; added a collapsible Backlog section and a read-only Release History section
- "Other" bucket holds in-progress features not yet assigned to an Initiative

**3.4 Authorization**
- Okta SSO Integration: serve the app inline under a host's URL via a path prefix (`BASE_PATH`), and trust the host's session via a session-introspection callback (`AUTH_INTROSPECT_URL`) instead of a separate login; works with Okta, SAML, or local host auth, and fails closed if the host is unreachable

**Bug fixes**
- A feature's flagged/owner/UAT-confirmed/raw-status were missing from several elements that open the feature panel (Feature Explorer results, Feature Registry, Roadmap's card types, Summary's WBS chart and Flagged Gaps list), so the panel could show stale defaults depending on which page opened it - every path now carries the same data
- The panel's Complete/Approve/Reject buttons now re-render the Flag button immediately after a status change instead of showing stale state until reload
- Structure page: renaming or removing a persona now cascades to any workflow that references it, instead of silently orphaning the reference
- Structure page: fixed dirty-tracking so the app-wide auto-reload no longer wipes out an unsaved section while another section is being saved
- Structure page: fixed a formatting bug where every section save collapsed the file's usual blank-line spacing around headings

## 0.5.0

**1.2 Features Board**
- Feature Explorer keyword search with ranked results
- WBS drag-and-drop reassignment within the feature panel
- Gap flagging: mark any non-terminal feature as a known gap

**1.4 Bug Tracker**
- Screenshot attachment: upload, preview, and lightbox view per bug

**3.3 Hosted Deployment**
- Session-based project upload for hosted / demo deployments
- Project download as ZIP
- In-memory session store; all writes scoped to session with no disk side effects

## 0.4.0

**3.2 Authentication**
- HTML login page replaces browser Basic Auth dialog
- HMAC-signed 30-day session cookies
- figtion YAML config at ~/.config/strategy-as-code/auth.yml
- Docker images ship with baked-in default auth.yml

## 0.3.0

**1.2 Features Board**
- Review column for Live features awaiting UAT sign-off
- Approve / Reject buttons advance or revert Live features
- Merged Scoped and Scored columns into single Prioritized column sorted by priority score
- Inline score clearing reverts Scored features to Scoped

**1.3 Roadmap**
- Next Release column groups Live features by sub-area using collapsible details
- Backlog rendered as flat list sorted by descending priority score

## 0.2.0

**1.2 Features Board**
- Feature panel stays open after saving a score
- Value and Effort scores can be cleared individually; partial clear reverts status to Scoped

**1.3 Roadmap**
- Version bucket grouping in the Planned section
- Next Release column derives version from in-progress changelog entry
- In-progress changelog sub-areas drive Next Release column membership for Live features

**2.1 Program Strategy Skill**
- WBS chart regeneration from within the skill
- Changelog entry generation from in-progress roadmap items

## 0.1.0

**1.1 Dashboard**
- Feature status summary with Gap / Planned / Live counts
- Recent changelog panel showing last 3 entries
- In-progress feature list

**1.2 Features Board**
- Five-column Kanban: Ideas, Prioritized, In Progress, Review, Released
- Inline feature panel with editing
- Priority score calculation (Value / Effort)
- Priority matrix scatter plot

**1.3 Roadmap**
- Four-column roadmap: Next Release, In Progress, Planned, Backlog
- Freeform backlog items from ABOUT.MD

**1.4 Bug Tracker**
- Active bug board with severity badges
- Resolve to history with version and date

**2.1 Program Strategy Skill**
- Feature lifecycle management via Claude Code
- Roadmap section editing via conversation

**2.2 Install & Integration**
- Global and per-project skill install scripts
- GitHub Actions workflow for ghcr.io image publish

**3.1 Deployment**
- Docker production and dev images
- Railway deployment config
- Pre-built ghcr.io multi-arch image

# Roadmap

## Initiatives

### PRD & OKR Tooling (Minor)
- 2.1.5
- 2.1.6

### Dashboard & Board Polish (Minor)
- 1.1.4
- 1.2.11

## Backlog
- WBS chart display in web UI
- OKR alignment mapping
- PRD generation command
- Sub-area burndown chart on dashboard
- Drag-and-drop feature reorder
- Multi-tenant session isolation
