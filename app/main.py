import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .config import settings, full
from .routers import pages, api
from .routers import auth_router
from .auth import is_authenticated
from . import git_sync
from . import github_issues
from . import hub_identity
from . import overview
from . import poller
from . import scope
from .fileio import ProjectReadOnly
from .registry import Registry, RegistryError, SyncConflict
from .writer import WriteConflict
from .template_env import templates

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
    stop = threading.Event()
    registry = scope.get_registry()
    if registry is not None and settings.program_poll_seconds > 0:
        threading.Thread(target=poller.poll_loop, args=(registry, settings.program_poll_seconds, stop),
                         daemon=True).start()
    yield
    stop.set()


app = FastAPI(title=settings.app_title, docs_url=full("/api-docs"), lifespan=_lifespan)

# Program mode: one clone per member project, served under <base>/p/<key>/.
if settings.program_manifest:
    scope.set_registry(Registry.from_manifest(Path(settings.program_manifest).expanduser()))


@app.get(full("/docs/{path:path}"), include_in_schema=False)
def project_docs(path: str):
    """The project's docs/ folder (WBS charts etc.), resolved per request so it follows
    the active project - including a project-scoped request."""
    root = settings.docs_dir.resolve()
    target = (root / path).resolve()
    if not target.is_file() or (root != target and root not in target.parents):
        raise HTTPException(status_code=404)
    return FileResponse(target)


# Serve local static assets
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount(full("/static"), StaticFiles(directory=str(static_dir)), name="static")

_PUBLIC = {full("/login"), full("/logout"), full("/hooks/refresh")}  # the webhook authenticates by signature
_SCOPE_RE = re.compile(rf"^{re.escape(settings.root_path)}/p/([^/]+)(/.*)?$")


def _plain(status: int, message: str) -> HTMLResponse:
    return HTMLResponse(f"<p style=\"font-family:sans-serif\">{message}</p>", status_code=status)


# Declared before require_auth so it sits inside it: auth sees the original
# /p/<key>/... path (and returns to it after login), the router sees the rewritten one.
@app.middleware("http")
async def scope_project(request: Request, call_next):
    m = _SCOPE_RE.match(request.scope["path"])
    registry = scope.get_registry()
    if not m:
        return await call_next(request)
    key, rest = m.group(1), m.group(2) or "/"
    if registry is None or key not in registry:
        return _plain(404, f"Unknown project {key!r}.")
    root = settings.root_path
    if rest == "/":
        return RedirectResponse(url=f"{root}/p/{key}/dashboard")
    try:
        stale = await run_in_threadpool(scope.freshen, key)
    except Exception as exc:
        logger.exception("cannot load project %s", key)
        return _plain(502, f"Could not load project {key!r}: {exc}")
    token = scope.current_project.set(key)
    request.scope["path"] = root + rest
    if "raw_path" in request.scope:
        request.scope["raw_path"] = (root + rest).encode()
    try:
        response = await call_next(request)
    finally:
        scope.current_project.reset(token)
    if stale:
        response.headers["X-Project-Stale"] = "1"
    return response


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if hub_identity.enabled() and request.url.path not in _PUBLIC:
        verdict = await hub_identity.lookup(request.headers.get("cookie"))
        if verdict.status == 401:
            login_url = settings.host_login_url or full("/login")
            return RedirectResponse(url=f"{login_url}?next={request.url.path}", status_code=303)
        if verdict.status != 200:
            return _plain(verdict.status, "You do not have access to Program Strategy." if verdict.status == 403
                          else "Sign-in service unavailable. Try again shortly.")
        request.state.identity = verdict.identity
        if hub_identity.is_write(request.method, request.url.path) and not hub_identity.may_write(verdict.identity):
            return JSONResponse({"detail": "Your role is read-only."}, status_code=403)
        return await call_next(request)
    if request.url.path not in _PUBLIC and not await is_authenticated(request):
        login_url = settings.host_login_url or full("/login")
        return RedirectResponse(url=f"{login_url}?next={request.url.path}", status_code=303)
    return await call_next(request)


app.include_router(auth_router.router, prefix=settings.base_path)
app.include_router(pages.router, prefix=settings.base_path)
app.include_router(api.router, prefix=full("/api"))


@app.exception_handler(ProjectReadOnly)
def _read_only(_request: Request, exc: ProjectReadOnly):
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(WriteConflict)
@app.exception_handler(SyncConflict)
def _conflict(_request: Request, exc: RegistryError):
    return JSONResponse({"detail": f"Could not save, please retry: {exc}"}, status_code=409)


@app.exception_handler(RegistryError)
def _sync_failed(_request: Request, exc: RegistryError):
    logger.error("program write failed: %s", exc)
    return JSONResponse({"detail": f"Could not save to the project repository: {exc}"}, status_code=502)


@app.post(full("/hooks/refresh"), include_in_schema=False)
async def refresh_hook(request: Request):
    """GitHub push webhook: refresh the project named by the event now. Disabled
    unless a webhook secret is configured; the signature is the only credential."""
    registry = scope.get_registry()
    if registry is None or not settings.webhook_secret:
        raise HTTPException(status_code=404)
    body = await request.body()
    if not poller.valid_signature(settings.webhook_secret, body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="bad signature")
    if request.headers.get("X-GitHub-Event") == "ping":
        return {"ok": True}
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON")
    result = await run_in_threadpool(poller.handle_push_event, registry, payload)
    return {"refreshed": sorted(k for k, err in result.items() if err is None),
            "failed": {k: err for k, err in result.items() if err}}


@app.get(full("/p"), include_in_schema=False)
async def projects_index(request: Request):
    if scope.get_registry() is None:
        raise HTTPException(status_code=404)
    rows = await run_in_threadpool(overview.summarize_all)
    initiatives, initiatives_error = overview.initiative_views(rows)
    return templates.TemplateResponse(request, "overview.html", {
        "projects": rows, "initiatives": initiatives, "initiatives_error": initiatives_error, "stages": overview.STAGES, "app_title": "Program overview"})


def root():
    # In program mode the front page is the overview; a single project goes straight to its dashboard.
    return RedirectResponse(url=full("/p") if scope.get_registry() is not None else full("/dashboard"))


# Served at "/" and at the configured prefix with and without its trailing slash: behind a proxy that does not strip
# the prefix (Renewals' /project-planning) the first request is "<prefix>/", which has no other route.
for _path in {"/", settings.root_path, f"{settings.root_path}/"} - {""}:
    app.get(_path, include_in_schema=False)(root)
