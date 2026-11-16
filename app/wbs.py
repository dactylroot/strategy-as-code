from __future__ import annotations
import os
import sys
import subprocess
from pathlib import Path


_SCRIPT = Path(__file__).parent.parent / "scripts" / "gen_wbs.py"


def regenerate(project_dir: Path) -> dict[str, str]:
    if not _SCRIPT.exists():
        raise FileNotFoundError(f"WBS script not found at {_SCRIPT}")

    env = os.environ.copy()
    env["PROJECT_DIR"] = str(project_dir)

    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "WBS generation failed")

    return {
        "png": str(project_dir / "docs" / "wbs.png"),
        "html": str(project_dir / "docs" / "wbs.html"),
    }


# ── On-demand chart ──────────────────────────────────────────────────────────
# The committed docs/wbs.* files are a snapshot. These render the chart from the current PRODUCT.MD
# text instead, and keep the last few results keyed by that text so a page view is not a 7 second run.
import hashlib
import shutil
import tempfile
import threading
from collections import OrderedDict

_CACHE: "OrderedDict[str, dict[str, bytes]]" = OrderedDict()
_CACHE_MAX = 8
_LOCK = threading.Lock()


def render(product_text: str, fallback_title: str = "") -> dict[str, bytes]:
    """{"png": bytes, "html": bytes} for this PRODUCT.MD text. Raises RuntimeError if the generator fails."""
    if not _SCRIPT.exists():
        raise FileNotFoundError(f"WBS script not found at {_SCRIPT}")
    key = hashlib.sha256((str(_SCRIPT.stat().st_mtime_ns) + "\0" + fallback_title + "\0" + product_text).encode()).hexdigest()
    with _LOCK:                                   # one generation at a time; a repeat request reuses the result
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return hit
        work = Path(tempfile.mkdtemp(prefix="pac_wbs_"))
        try:
            project = work / (fallback_title or "project")
            project.mkdir()
            (project / "PRODUCT.MD").write_text(product_text, encoding="utf-8")
            regenerate(project)
            out = {"png": (project / "docs" / "wbs.png").read_bytes(), "html": (project / "docs" / "wbs.html").read_bytes()}
        finally:
            shutil.rmtree(work, ignore_errors=True)
        _CACHE[key] = out
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
        return out
