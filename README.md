# strategy-as-code

Core information for communication and design on project planning.

Each interface type is supported by a respective markdown file.

Other workflows and reports for stakeholders, engineers, and end-users derive from these sources of truth.

Interface with: 

| File         | Purpose                                   | Audience                      | Interface                        |
| ------       | ---------                                 | ----------                    | -----------                      |
| `PRODUCT.MD` | Overview, WBS structure, feature registry | Stakeholders                  | Web UI                           |
| `README.MD`  | Operational and dev documentation         | Operational and dev engineers | Claude skill & Markdown          |
| `ABOUT.MD`   | Changelog and roadmap                     | Users                         | Markdown for app & documentation |
| `BUGS.MD`    | Bug tracking and triage                   | All                           | Markdown & Web UI                |

## Screenshots

**Summary** - feature counts, completion progress, and recent changelog

![Summary](sample-project/screenshots/dashboard.png)

**Features** - five-column Kanban with WBS structure sidebar

![Features](sample-project/screenshots/features.png)

**Roadmap** - Next Release, In Progress, Planned buckets, and Backlog

![Roadmap](sample-project/screenshots/roadmap.png)

**Bugs** - active bug board with severity badges and resolved history

![Bugs](sample-project/screenshots/bugs.png)

## Install the Claude skill

The `program-strategy` skill teaches Claude Code to manage your product documentation using the four-file format above.

**Global install** (available in all projects):

```bash
git clone https://github.com/dactylroot/strategy-as-code
cd strategy-as-code
./scripts/install-skill.sh
```

**Per-project install** (available only in one project):

```bash
./scripts/install-skill.sh /path/to/your/project
```

The script symlinks `.claude/skills/program-strategy/` into the target skill directory. Once installed, open any Claude Code session in the target project and invoke it with:

```
/program-strategy
```

The skill manages the four markdown files directly - reading and editing them in whichever directory the Claude Code session is open in.

To launch the web UI from within a skill session, tell Claude to run the UI:

```
run the UI
```

Claude will start the server pointed at the current project directory and open `http://localhost:8765` in your browser. The UI reads files on each page load, so edits the skill makes are visible immediately without restarting.

## Run the UI

### Local (Python)

Requires Python 3.12 and [pyenv](https://github.com/pyenv/pyenv).

```bash
pip install -r requirements.txt
PROJECT_DIR=/path/to/your/project ./run.sh
```

Runs on `http://localhost:8765`. Set `APP_TITLE` to override the page title:

```bash
APP_TITLE="My Product" PROJECT_DIR=/path/to/your/project ./run.sh
```

### Docker

Copy `.env.example` to `.env` and set your project path:

```bash
cp .env.example .env
# Edit .env: PROJECT_SOURCE_PATH=/path/to/your/project
```

Then start the container:

```bash
docker-compose up
```

Runs on `http://localhost:8765`.

### Pre-built image (ghcr.io)

```bash
docker run -p 8765:8000 \
  -v /path/to/your/project:/project \
  ghcr.io/dactylroot/strategy-as-code:latest
```

Supports `linux/amd64` and `linux/arm64`.

#### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_DIR` | `/project` | Path to project inside the container |
| `APP_TITLE` | Derived from `PRODUCT.MD` | UI page title |
| `PORT` | `8000` | Port to bind |
| `GIT_REPO_URL` | | Git repo to clone on first run |
| `GIT_TOKEN` | | HTTPS token for push/sync |
| `GIT_USER_NAME` | | Git commit author name |
| `GIT_USER_EMAIL` | | Git commit author email |
| `AUTH_CONFIG` | `~/.config/strategy-as-code/auth.yml` | Path to local-login config (`enabled`, `username`, `password`, `secret_key`) |
| `SERVER_CONFIG` | `~/.config/strategy-as-code/server.yml` | Path to deployment config (`project_dir`, `lock_project`, `app_title`) |
| `BASE_PATH` | (empty) | URL prefix to serve under, e.g. `/strategy` - see [Embedded](#embedded) below |
| `AUTH_INTROSPECT_URL` | (empty) | Host session-introspection endpoint - see [Embedded](#embedded) below |
| `HOST_LOGIN_URL` | (empty) | Host's login page, used as the redirect target in embedded mode |

### Deployment modes

The app runs in one of three modes, chosen by which of the variables above are set.

#### Standalone

The default. The app owns its own domain/port and handles its own login against `auth.yml`.

```bash
PROJECT_DIR=/path/to/your/project ./run.sh
```

#### Locked

Pins the UI to a single project directory and hides the "Switch Project" screen. Combine with either standalone or embedded mode. Set `lock_project: true` in `server.yml` (path from `SERVER_CONFIG`):

```yaml
project_dir: /path/to/your/project
lock_project: true
```

#### Embedded

Serves the app inline under a host project's own domain at a path prefix, trusting the host's session instead of showing a second login screen.

```bash
BASE_PATH=/strategy \
AUTH_INTROSPECT_URL=http://host-app:8080/internal/whoami \
HOST_LOGIN_URL=/login \
PROJECT_DIR=/path/to/your/project ./run.sh
```

- `BASE_PATH` prefixes every route, redirect, and link so the app resolves correctly behind a reverse proxy forwarding `host.com/strategy/*` to it.
- `AUTH_INTROSPECT_URL` points at an endpoint on the host that validates its own session cookie, however the host authenticates its users (Okta or otherwise), and returns `200` or `401`. Setting this replaces local login entirely, and access fails closed if the host is unreachable.
- `HOST_LOGIN_URL` is where unauthenticated visitors get redirected, so there's exactly one login screen for the combined experience.
