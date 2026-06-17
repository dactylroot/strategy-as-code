import os
import tempfile

# Must be set before any app.auth import occurs (figtion reads the file at module load).
_auth_fd, _auth_tmp = tempfile.mkstemp(suffix=".yml")
os.close(_auth_fd)
os.environ["AUTH_CONFIG"] = _auth_tmp

import pytest
from pathlib import Path
from fastapi.testclient import TestClient


PRODUCT_MD = """\
# Test Product - Product Overview

## Summary
A product used for unit testing.

## Users

### Admin
Manages the system.

### Viewer
Read-only access.

## Product Scope

### Core Functionality
- Login and authentication
- ~~Dashboard overview~~ 🎆

## Core Workflows

### Admin Workflow
1. Log in
2. Manage features

## Features

### 1. Core

#### 1.1 Auth

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.1.1 | Login | Live | 8 | 3 | Auth login form |
| 1.1.2 | Logout | Scoped | | | Logout flow |
| 1.1.3 | Password reset | Gap | | | Not started |

#### 1.2 Dashboard

| WBS | Feature | Status | Value | Effort | Notes |
| --- | ------- | ------ | ----- | ------ | ----- |
| 1.2.1 | Overview | Scored | 7 | 2 | Main dashboard |
| 1.2.2 | Stats panel | In-Progress | | | Currently building |
| 1.2.3 | Export | Idea | | | |

## Known Gaps for Team Discussion

### Password Reset
Users cannot reset their password.
"""

ABOUT_MD = """\
# Changelog

## 0.2.0 (in progress)

**1.1 Auth**
- Login form implemented
- Session cookies

## 0.1.0

**1.1 Auth**
- Initial project setup

**Bug fixes**
- Fixed startup crash

# Roadmap

## In Progress
- 1.2 Dashboard

## Planned
- 1.3 Reporting
- 1.4 Admin Tools

## Backlog
- Dark mode
- Mobile app
"""


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    (tmp_path / "PRODUCT.MD").write_text(PRODUCT_MD, encoding="utf-8")
    (tmp_path / "ABOUT.MD").write_text(ABOUT_MD, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(tmp_project: Path, monkeypatch):
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_runtime_project_dir", tmp_project)

    from app.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
