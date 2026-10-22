from pathlib import Path
import markdown as md
from fastapi.templating import Jinja2Templates

from . import scope
from .config import settings

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))
templates.env.filters["markdown_to_html"] = lambda text: md.markdown(
    text, extensions=["tables", "fenced_code"]
)


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
templates.env.globals["projects_url"] = lambda: f"{settings.root_path}/p"
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
