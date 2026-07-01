from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import check_credentials, make_cookie, COOKIE_NAME
from ..config import full
from ..template_env import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, next: str = ""):
    return templates.TemplateResponse(request, "login.html", {"next": next or full("/dashboard")})


@router.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
):
    if not check_credentials(username, password):
        return templates.TemplateResponse(
            request, "login.html",
            {"next": next or full("/dashboard"), "error": "Invalid username or password."},
            status_code=401,
        )
    # Redirect to the originally-requested page and set auth cookie
    dest = next if next.startswith("/") else full("/dashboard")
    response = RedirectResponse(url=dest, status_code=303)
    response.set_cookie(
        key=COOKIE_NAME,
        value=make_cookie(username),
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,  # 30 days
    )
    return response


@router.get("/logout", include_in_schema=False)
def logout():
    response = RedirectResponse(url=full("/login"), status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response
