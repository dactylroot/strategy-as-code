import os
import re
import figtion
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    project_dir: Path = Path("/project")
    app_title: str = "Project Docs"
    git_user_name: str = "strategy-as-code"
    git_user_email: str = "noreply@strategy-as-code"
    source_path: str = ""  # Optional human-readable host path shown in the UI
    base_path: str = ""  # URL prefix this app is served under, e.g. "/strategy"
    auth_introspect_url: str = ""  # Host session-introspection endpoint; enables auth passthrough
    host_login_url: str = ""  # Host's login entry point; redirect target in passthrough mode

    model_config = {"env_prefix": ""}


# ── Deployment config (figtion YAML, same pattern as auth.py) ─────────────
_SERVER_CONFIG_PATH = Path(os.environ.get(
    "SERVER_CONFIG",
    Path.home() / ".config" / "strategy-as-code" / "server.yml",
))
_SERVER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

_deploy = figtion.Config(
    filepath=str(_SERVER_CONFIG_PATH),
    defaults={
        "project_dir": "",      # absolute path; overrides PROJECT_DIR env var
        "lock_project": False,  # disable Switch Project UI when True
        "app_title": "",        # override title; empty = read from PRODUCT.MD
    },
    verbose=False,
)

_base = Settings()

def _title_from_product_md(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
        m = re.match(r"^# (.+)", text)
        raw = m.group(1).strip() if m else ""
        return re.sub(r"\s*[-–]\s*(Product\s+)?Overview\s*$", "", raw, flags=re.IGNORECASE).strip() or None
    except Exception:
        return None

# Effective startup project dir: server.yml > PROJECT_DIR env var
_startup_dir: Path = _base.project_dir
if _deploy["project_dir"]:
    _d = Path(str(_deploy["project_dir"])).expanduser().resolve()
    if _d.exists():
        _startup_dir = _d

# Runtime-mutable state lives outside the frozen Settings model
_runtime_project_dir: Path | None = _startup_dir if _startup_dir != _base.project_dir else None

# Effective startup title: server.yml > APP_TITLE env var > PRODUCT.MD
if _deploy["app_title"]:
    _runtime_app_title: str | None = str(_deploy["app_title"])
elif "APP_TITLE" in os.environ:
    _runtime_app_title = None
else:
    _runtime_app_title = _title_from_product_md(_startup_dir / "PRODUCT.MD") or _startup_dir.name

_recent_projects: list[dict] = []  # {"path": str, "title": str, "uploaded": bool}
_is_uploaded: bool = False


def _effective_project_dir() -> Path:
    return _runtime_project_dir or _base.project_dir


def _effective_app_title() -> str:
    return _runtime_app_title or _base.app_title


def switch_project(new_dir: Path, *, uploaded: bool = False) -> None:
    global _runtime_project_dir, _runtime_app_title, _recent_projects, _is_uploaded
    resolved = new_dir.resolve()
    if not (resolved / "PRODUCT.MD").exists():
        raise ValueError(f"PRODUCT.MD not found in {resolved}")
    old_path     = str(_effective_project_dir())
    old_title    = _effective_app_title()
    old_uploaded = _is_uploaded
    # Derive title from new PRODUCT.MD
    _runtime_app_title = _title_from_product_md(resolved / "PRODUCT.MD") or resolved.name
    # Update recent list: deduplicate by title (same title = same project)
    _recent_projects = [
        r for r in _recent_projects
        if r["title"] != old_title and r["path"] != str(resolved)
    ]
    _recent_projects.insert(0, {"path": old_path, "title": old_title, "uploaded": old_uploaded})
    _recent_projects = _recent_projects[:5]
    _is_uploaded = uploaded
    _runtime_project_dir = resolved


# Proxy object so all consumers can still use `settings.product_md` etc.
class _SettingsProxy:
    @property
    def project_dir(self) -> Path:
        return _effective_project_dir()

    @property
    def app_title(self) -> str:
        return _effective_app_title()

    @property
    def product_md(self) -> Path:
        return self.project_dir / "PRODUCT.MD"

    @property
    def about_md(self) -> Path:
        return self.project_dir / "ABOUT.MD"

    @property
    def readme_md(self) -> Path:
        return self.project_dir / "README.MD"

    @property
    def docs_dir(self) -> Path:
        return self.project_dir / "docs"

    @property
    def bugs_md(self) -> Path:
        return self.project_dir / "BUGS.MD"

    @property
    def git_user_name(self) -> str:
        return _base.git_user_name

    @property
    def git_user_email(self) -> str:
        return _base.git_user_email

    @property
    def source_path(self) -> str:
        return _base.source_path or str(self.project_dir)

    def get_project_dir(self) -> Path:
        return self.project_dir

    @property
    def is_uploaded(self) -> bool:
        return _is_uploaded

    @property
    def recent_projects(self) -> list[dict]:
        return _recent_projects

    @property
    def lock_project(self) -> bool:
        return bool(_deploy["lock_project"])

    @property
    def base_path(self) -> str:
        v = _base.base_path.strip("/")
        return f"/{v}" if v else ""

    @property
    def auth_introspect_url(self) -> str:
        return _base.auth_introspect_url

    @property
    def host_login_url(self) -> str:
        return _base.host_login_url


settings = _SettingsProxy()  # type: ignore[assignment]


def full(path: str) -> str:
    """Prefix a root-relative path (e.g. "/dashboard") with the configured base_path."""
    return f"{settings.base_path}{path}"
