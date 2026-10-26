import html
import re
from pathlib import Path
import markdown as md
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import scope
from .config import settings

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))
templates.env.filters["markdown_to_html"] = lambda text: md.markdown(
    text, extensions=["tables", "fenced_code"]
)


def _inline_markdown(text: str):
    """Bold and `code` spans of a summary line as HTML, everything else escaped: repository text is never trusted markup."""
    escaped = html.escape(text or "")
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return Markup(escaped)


templates.env.filters["inline_markdown"] = _inline_markdown


class _BasePath:
    """Rendered per use, so links carry /p/<key> while a project-scoped request is served."""
    def __str__(self) -> str:
        return settings.base_path

    def __add__(self, other):
        return str(self) + str(other)

    def __radd__(self, other):
        return str(other) + str(self)


templates.env.globals["base_path"] = _BasePath()
templates.env.globals["scope_key"] = scope.scope_key
templates.env.globals["scope_branch"] = scope.branch_label
templates.env.globals["program_mode"] = lambda: scope.get_registry() is not None
templates.env.globals["projects_url"] = lambda: f"{settings.root_path}/p"


def _back_link() -> tuple[str, str] | None:
    """Where the header title returns to on a project page: the project's own web UI (the manifest's
    App URL), for everyone. The sidebar's All projects link is the way back to the program. None = the default."""
    registry, key = scope.get_registry(), scope.scope_key()
    if registry is None or key is None or key not in registry:
        return None
    spec = registry.get(key).spec
    return (spec.app_url, f"Back to {key.replace('-', ' ').title()}") if spec.app_url else None


templates.env.globals["back_link"] = _back_link
templates.env.globals["work_url"] = lambda: f"{settings.root_path}/work"
templates.env.globals["embed_chrome"] = settings.embed_chrome


def _wbs_header_color(pct: float) -> str:
    """Interpolate red→blue based on completion (matches gen_wbs.py palette)."""
    pct = max(0.0, min(1.0, float(pct)))
    r = int(185 + (30  - 185) * pct)
    g = int(28  + (64  -  28) * pct)
    b = int(28  + (175 -  28) * pct)
    return f"rgb({r},{g},{b})"


def _wbs_body_color(pct: float) -> str:
    """Tinted version of header color (15% saturation + 85% white)."""
    pct = max(0.0, min(1.0, float(pct)))
    r = int(185 + (30  - 185) * pct)
    g = int(28  + (64  -  28) * pct)
    b = int(28  + (175 -  28) * pct)
    return f"rgb({int(r*.15+255*.85)},{int(g*.15+255*.85)},{int(b*.15+255*.85)})"


templates.env.filters["wbs_header_color"] = _wbs_header_color
templates.env.filters["wbs_body_color"]   = _wbs_body_color
