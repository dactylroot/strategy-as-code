from pathlib import Path
import markdown as md


def render(path: Path) -> str:
    if not path.exists():
        return "<p><em>README.MD not found.</em></p>"
    text = path.read_text(encoding="utf-8")
    return md.markdown(text, extensions=["tables", "fenced_code", "toc", "nl2br"])
