import re
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    project_dir: Path = Path("/project")
    app_title: str = "Project Docs"
    git_user_name: str = "strategy-as-code"
    git_user_email: str = "noreply@strategy-as-code"
    source_path: str = ""  # Optional human-readable host path shown in the UI

    model_config = {"env_prefix": ""}


_base = Settings()

# Runtime-mutable state lives outside the frozen Settings model
_runtime_project_dir: Path | None = None
_runtime_app_title: str | None = None
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
    try:
        text = (resolved / "PRODUCT.MD").read_text(encoding="utf-8")
        m = re.match(r"^# (.+)", text)
        raw = m.group(1).strip() if m else resolved.name
        _runtime_app_title = re.sub(r"\s*[-–]\s*(Product\s+)?Overview\s*$", "", raw, flags=re.IGNORECASE).strip()
    except Exception:
        _runtime_app_title = resolved.name
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


settings = _SettingsProxy()  # type: ignore[assignment]
