from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from .config import settings, full
from .routers import pages, api
from .routers import auth_router
from .auth import is_authenticated

app = FastAPI(title=settings.app_title, docs_url=full("/api-docs"))

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
