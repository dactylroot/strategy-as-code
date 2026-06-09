from __future__ import annotations
import os
import subprocess
from pathlib import Path


_SCRIPT = Path(__file__).parent.parent / "scripts" / "gen_wbs.py"


def regenerate(project_dir: Path) -> dict[str, str]:
    if not _SCRIPT.exists():
        raise FileNotFoundError(f"WBS script not found at {_SCRIPT}")

    env = os.environ.copy()
    env["PROJECT_DIR"] = str(project_dir)

    result = subprocess.run(
        ["python", str(_SCRIPT)],
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
