from pathlib import Path
import markdown as md
from fastapi.templating import Jinja2Templates

from .config import settings

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))
templates.env.filters["markdown_to_html"] = lambda text: md.markdown(
    text, extensions=["tables", "fenced_code"]
)
templates.env.globals["base_path"] = settings.base_path


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
