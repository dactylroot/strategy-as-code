# strategy-as-code

Product management in plain markdown, with AI-assisted planning via Claude Code and a live web UI for stakeholders.

## Quick Start

```bash
pip install -r requirements.txt
PROJECT_DIR=/path/to/your/project ./run.sh
```

Or with Docker:

```bash
docker run -p 8765:8000 \
  -v /path/to/your/project:/project \
  ghcr.io/dactylroot/strategy-as-code:latest
```

## The Four Files

| File | Audience | Content |
|------|----------|---------|
| `PRODUCT.MD` | Stakeholders | Feature registry, WBS structure, users, scope |
| `ABOUT.MD` | Users | Changelog and roadmap |
| `README.MD` | Engineers | Setup, architecture, operations |
| `BUGS.MD` | Team | Bug tracking and triage |

## Architecture

FastAPI backend + Jinja2 templates + HTMX. File changes trigger SSE live-reload. No database — all state lives in the four markdown files.

## Authentication

Configured via `~/.config/strategy-as-code/auth.yml` or `AUTH_CONFIG` env var:

```yaml
enabled: true
username: admin
password: changeme
secret_key: ""  # auto-generated on first run
```
