# Changelog

## 0.4.0 (in progress)

**3.2 Authentication**
- HTML login page replaces browser Basic Auth dialog
- HMAC-signed 30-day session cookies
- figtion YAML config at ~/.config/strategy-as-code/auth.yml
- Docker images ship with baked-in default auth.yml

## 0.3.0

**1.2 Features Board**
- Review column for Live features awaiting UAT sign-off
- Approve button advances features to Released status
- Merged Scoped and Scored columns into single Prioritized column sorted by priority score
- Inline score clearing reverts Scored features to Scoped

**1.3 Roadmap**
- Next Release column groups Live features by sub-area using collapsible details
- Backlog rendered as flat list sorted by descending priority score
- Removed strikethrough from Live features in Next Release and Completed columns

## 0.2.0

**1.2 Features Board**
- Feature panel stays open after saving a score (SSE reload suppressed while panel is open)
- Value and Effort scores can be cleared individually; partial clear reverts status to Scoped

**1.3 Roadmap**
- Version bucket grouping in the Planned section
- Next Release column derives version from in-progress changelog entry
- In-progress changelog sub-areas drive Next Release column membership for Live features

**2.1 Program Strategy Skill**
- WBS chart regeneration from within the skill
- Changelog entry generation from in-progress roadmap items

**Bug fixes**
- Fixed Next Release showing v0.3.0 when v0.2.0 was already in-progress
- Fixed Next Release column missing Live features from in-progress changelog sub-areas

## 0.1.0

**1.1 Dashboard**
- Feature status summary with Gap / Planned / Live counts
- Recent changelog panel showing last 3 entries
- In-progress feature list

**1.2 Features Board**
- Five-column Kanban: Ideas, Prioritized, In Progress, Review, Released
- Inline feature panel with HTMX-powered editing
- Priority score calculation (Value / Effort)
- Priority matrix scatter plot

**1.3 Roadmap**
- Four-column roadmap: Next Release, In Progress, Planned, Backlog
- Freeform backlog items from ABOUT.MD

**1.4 Bug Tracker**
- Active bug board with severity badges
- Resolve to history with version and date

**2.2 Install & Integration**
- Global and per-project skill install scripts
- GitHub Actions workflow for ghcr.io image publish

**3.1 Deployment**
- Docker production and dev images
- Railway deployment config
- Pre-built ghcr.io multi-arch image

# Roadmap

## In Progress
- 3.2 Authentication

## Planned
### v0.5.0
- 2.1.5
- 2.1.6

### v0.6.0
- 1.2.6
- 1.4.4

## Backlog
- OKR alignment mapping
- PRD generation command
- Sub-area burndown chart on dashboard
- Drag-and-drop feature reorder
- Mobile-responsive layout
