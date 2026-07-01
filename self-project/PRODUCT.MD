# strategy-as-code - Product Overview

## Summary
strategy-as-code is a product management tool for engineering teams that keeps roadmaps, features, and changelogs in plain markdown files. A Claude skill handles AI-assisted planning directly in the terminal, and a FastAPI web UI gives stakeholders a live view of product state without needing access to the repository.

## Users

### Product Manager
Maintains the feature backlog, sets priorities, and tracks releases. Works through the web UI to score features, move items through the lifecycle, and communicate roadmap state to leadership.

### Engineering Lead
Reviews incoming feature requests, marks features in-progress, logs bugs, and cuts releases. Uses the Claude skill in the terminal to draft changelog entries and update roadmap sections without leaving the editor.

### Stakeholder
Reads the roadmap and changelog to understand what is shipping and when. Accesses the web UI directly; never touches markdown files.

## Product Scope

### Core Functionality
- ~~Four-file markdown schema (PRODUCT, ABOUT, README, BUGS)~~ 🎆
- ~~Web UI with live-reload on file changes~~ 🎆
- ~~Feature lifecycle (Gap / Idea → [Scoped] → [Scored] → In-Progress → Live → Released, with Scoped/Scored derived from Notes and Value/Effort rather than stored)~~ 🎆
- ~~Value / Effort scoring with priority score calculation~~ 🎆
- ~~Feature panel with inline editing~~ 🎆
- ~~Release workflow with changelog automation~~ 🎆
- WBS chart generation (PNG + interactive HTML)

### Planning & Roadmap
- ~~Roadmap page with Next Release, In Progress, and Backlog columns~~ 🎆
- ~~Version bucket grouping in the Planned section~~ 🎆
- ~~Semantic versioning derived from WBS sub-area completion~~ 🎆
- ~~Review column for Live features awaiting UAT sign-off~~ 🎆

### Collaboration
- ~~Claude skill for AI-assisted product planning~~ 🎆
- ~~Single-user authentication (figtion-based YAML config)~~ 🎆
- ~~Docker image with ghcr.io publish workflow~~ 🎆
- ~~Session-based project upload for hosted deployments~~ 🎆

### Bug Tracking
- ~~Bug lifecycle (Open → Investigating → Fix In Progress → Resolved)~~ 🎆
- ~~Severity tiers (Critical / High / Medium / Low)~~ 🎆
- ~~Bug screenshot attachments~~ 🎆
- ~~WBS cross-reference for bugs~~ 🎆

## Core Workflows

### Product Manager Workflow
1. Open the web UI and navigate to Features
2. Review the Ideas column for newly captured requests
3. Scope features by adding descriptions - they read as Scoped automatically once notes are saved
4. Score features with Value and Effort to compute priority
5. Navigate to Roadmap to sequence upcoming releases
6. Cut a release via the UI when In Progress items are Live

### Engineering Lead Workflow
1. Open a Claude Code session in the project repo
2. Invoke /program-strategy and describe the work
3. Claude updates PRODUCT.MD, ABOUT.MD, and BUGS.MD
4. Push changes - the live UI reflects updates immediately

## Features

### 1. Web UI

#### 1.1 Dashboard

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.1.1 | Feature status summary cards | Live | 8 | 2 | Gap / Planned / Live counts with progress bar |
| 1.1.2 | Recent changelog panel | Live | 6 | 1 | Last 3 changelog entries |
| 1.1.3 | In-progress features list | Live | 7 | 2 | Features currently being built |
| 1.1.4 | Sub-area completion chart | Idea | 5 | 4 | Burndown by WBS sub-area |

#### 1.2 Features Board

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.2.1 | Five-column Kanban board | Live | 9 | 4 | Ideas, Prioritized, In Progress, Review, Released |
| 1.2.2 | Inline feature panel overlay | Live | 9 | 5 | Edit name, status, score, notes; WBS reassignment; gap flagging |
| 1.2.3 | Priority score sorting | Live | 8 | 2 | Prioritized column sorted by Value/Effort ratio |
| 1.2.4 | Priority matrix scatter plot | Live | 6 | 3 | Visual Value vs Effort quadrant for scored features |
| 1.2.5 | Review column with Approve / Reject | Live | 8 | 3 | Advance or reject Live features after UAT |
| 1.2.6 | Feature Explorer search | Live | 8 | 3 | Keyword search across name and notes with ranked results |
| 1.2.7 | WBS reassignment in panel | Live | 7 | 3 | Drag token or click sub-area to move feature across WBS |
| 1.2.8 | Feature deletion with confirmation | Live | 6 | 2 | Requires typing the feature name to confirm; removes from PRODUCT.MD |
| 1.2.9 | Search state preserved after reload | Live | 5 | 1 | sessionStorage restores active query after panel-triggered page reload |
| 1.2.10 | In Progress action from any stage | Live | 7 | 2 | → In Progress button visible on Scoped and Scored cards and in panel |
| 1.2.11 | Drag-and-drop card reorder | Idea | 5 | 6 | SortableJS within Kanban column |

#### 1.3 Roadmap

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.3.1 | Next Release column | Live | 9 | 3 | Shows pending version with Live features grouped by sub-area |
| 1.3.2 | In Progress column | Live | 8 | 2 | Features currently in flight |
| 1.3.3 | Planned version buckets | Live | 7 | 3 | Group planned sub-areas under target versions |
| 1.3.4 | Backlog sorted by priority | Live | 7 | 2 | Flat list ordered by descending priority score |
| 1.3.5 | Freeform backlog items | Live | 5 | 1 | Free-text items from ABOUT.MD backlog section |

#### 1.4 Bug Tracker

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.4.1 | Active bug board | Live | 8 | 3 | Columns for Open, Investigating, Fix In Progress |
| 1.4.2 | Bug severity badges | Live | 6 | 1 | Critical / High / Medium / Low color-coded |
| 1.4.3 | Resolve to history | Live | 7 | 2 | Move bug to resolved table with version and date |
| 1.4.4 | Screenshot attachment | Live | 6 | 3 | Upload, preview, and lightbox view per bug |
| 1.4.5 | WBS cross-reference | Live | 5 | 1 | Link bug to a feature WBS code |

#### 1.5 Attribution

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.5.1 | Feature requester tracking | Idea | | | Record who submitted each feature request |
| 1.5.2 | Bug reporter tracking | Idea | | | Record who reported each bug |

### 2. Claude Skill

#### 2.1 Program Strategy Skill

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 2.1.1 | Feature lifecycle management | Live | 9 | 3 | Gap through Released via Claude Code |
| 2.1.2 | Roadmap section editing | Live | 8 | 3 | In Progress / Planned / Backlog via conversation |
| 2.1.3 | Changelog entry generation | Live | 8 | 2 | Drafts release notes from in-progress items |
| 2.1.4 | WBS chart regeneration | Live | 6 | 2 | Triggers PNG and HTML chart rebuild |
| 2.1.5 | PRD generation | Idea | 7 | 4 | Generate structured PRD docs from feature descriptions |
| 2.1.6 | OKR alignment | Idea | 6 | 5 | Map features to OKRs and surface gaps |

#### 2.2 Install & Integration

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 2.2.1 | Global skill install script | Live | 8 | 2 | Symlinks skill into ~/.claude/skills/ |
| 2.2.2 | Per-project install | Live | 7 | 2 | Symlinks into .claude/skills/ in project root |
| 2.2.3 | GitHub Actions publish workflow | Live | 7 | 3 | Builds and pushes ghcr.io image on tag |

### 3. Infrastructure

#### 3.1 Deployment

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 3.1.1 | Docker production image | Live | 9 | 3 | Auth enabled, project seeded from GIT_REPO_URL |
| 3.1.2 | Docker dev image | Live | 7 | 2 | Auth disabled, live-reload with volume mounts |
| 3.1.3 | Railway deployment config | Live | 7 | 2 | railway.toml with healthcheck |
| 3.1.4 | Pre-built ghcr.io image | Live | 8 | 2 | amd64 + arm64 multi-arch |

#### 3.2 Authentication

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 3.2.1 | HTML login page | Live | 8 | 2 | Custom styled login, no browser Basic Auth popup |
| 3.2.2 | HMAC-signed session cookies | Live | 8 | 3 | 30-day HTTP-only cookies |
| 3.2.3 | figtion YAML config | Live | 7 | 2 | Credentials and secret key in auth.yml |
| 3.2.4 | Docker default auth config | Live | 6 | 1 | Baked-in auth.yml; enabled=true in prod image |

#### 3.3 Hosted Deployment

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 3.3.1 | Session-based project upload | Live | 8 | 3 | Upload ZIP of .md files; runs in isolated in-memory session |
| 3.3.2 | Project download as ZIP | Live | 6 | 2 | Export current session or disk project as .zip |
| 3.3.3 | In-memory session store | Live | 7 | 3 | All writes stay in session; no disk side effects in hosted mode |
| 3.3.4 | Switch project (disk mode) | Live | 5 | 2 | API endpoint to change the active PROJECT_DIR at runtime |
| 3.3.5 | Multi-tenant isolation | Idea | 7 | 7 | Per-user session scoping with auth integration |

#### 3.4 Authorization

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 3.4.1 | Role-based access control | Idea | | | Separate idea-submit, read-only, and admin roles; no write or delete for submitters |
| 3.4.2 | Okta SSO Integration | Live | | | Serves the app inline under a host project's own URL via a configurable path prefix (BASE_PATH), and trusts the host's existing session via a session-introspection callback (AUTH_INTROSPECT_URL) instead of showing a separate login screen; works regardless of whether the host uses Okta, SAML, or local auth. Fails closed if the host is unreachable. Role-based permission inheritance is a separate, still-open item under 3.4.1. |

#### 3.5 Data Management

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 3.5.1 | Incremental file autosave | Idea | | | Reduce data loss window for disk-mode projects |
| 3.5.2 | Git branch auto-commit | Idea | | | Commit file changes to a tracking branch when the project dir is a git repo |

## Known Gaps for Team Discussion

### WBS Chart Generation
The /api/wbs/regenerate endpoint exists but the chart output (PNG + interactive HTML) is not surfaced in the web UI. A dashboard widget or dedicated chart page would complete this.

### OKR Alignment
No structured way to map features to OKRs or goals. A lightweight OKR section in PRODUCT.MD and a skill command to surface alignment gaps would close this.

### PRD Generation
The skill can describe features but cannot yet produce a structured PRD document. Adding a /prd command that generates a stakeholder-ready document from PRODUCT.MD would reduce the handoff cost for feature requests.
