import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from .config import settings, full
from .routers import pages, api
from .routers import auth_router
from .auth import is_authenticated
from . import git_sync
from . import github_issues

logger = logging.getLogger(__name__)


def _periodic_sync_loop() -> None:
    # Catches drift from any writer of the synced paths (e.g. renewals'
    # bug_report_service.py, which has no git/GitHub awareness of its own),
    # not just edits made through this app's own UI.
    #
    # Each leg is guarded independently: a GitHub API hiccup must never take out
    # the content-sync push, which is the only thing carrying edits out of this
    # ephemeral container. An unguarded raise here would kill the whole thread -
    # and with it every subsequent sync until the next container restart.
    while True:
        time.sleep(max(settings.git_sync_poll_seconds, 5))
        try:
            if settings.github_repo:
                github_issues.create_missing_issues()
                github_issues.close_resolved_issues()
        except Exception:
            logger.exception("periodic GitHub issue sync failed; continuing")
        try:
            if settings.git_sync_enabled:
                git_sync.sync_now("periodic")
        except Exception:
            logger.exception("periodic git content-sync failed; continuing")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    if settings.git_sync_enabled or settings.github_repo:
        threading.Thread(target=_periodic_sync_loop, daemon=True).start()
    yield


app = FastAPI(title=settings.app_title, docs_url=full("/api-docs"), lifespan=_lifespan)

# Serve static files from the project's docs/ directory (WBS charts etc.)
docs_dir = settings.docs_dir
if docs_dir.exists():
    app.mount(full("/docs"), StaticFiles(directory=str(docs_dir)), name="docs")

# Serve local static assets
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount(full("/static"), StaticFiles(directory=str(static_dir)), name="static")

_PUBLIC = {full("/login"), full("/logout")}


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if request.url.path not in _PUBLIC and not await is_authenticated(request):
        login_url = settings.host_login_url or full("/login")
        return RedirectResponse(url=f"{login_url}?next={request.url.path}", status_code=303)
    return await call_next(request)


app.include_router(auth_router.router, prefix=settings.base_path)
app.include_router(pages.router, prefix=settings.base_path)
app.include_router(api.router, prefix=full("/api"))


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url=full("/dashboard"))
