"""Identity and roles from the RevOps hub.

In hub mode (`hub_auth_url` set) the only identity this app accepts is the
hub's own answer to `GET /internal/auth?app=strategy`, asked by this server with
the browser's session cookie forwarded. Headers a client sends for identity
(`X-Host-User`, `X-User-Email`, `X-App-Role`) are ignored, so nothing depends on
a proxy stripping them.

Roles: viewer reads; editor reads and writes (edits, submissions, triage);
admin also manages projects and the manifest (reserved, nothing needs it yet);
contributor is a Power User of one project's app: it opens that project's Features and Bugs pages,
submits to the inbox, and changes only its own untriaged submissions (see CONTRIBUTOR_ROUTES);
reporter is anyone else with a grant in that app: it reports bugs and reads its own submissions, from the
app's About page, and opens no page here (see REPORTER_ROUTES).
The hub is asked on every request, with a few seconds of caching per cookie, so
a revoked grant stops working within the TTL. Any hub failure denies access.
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request

from .config import settings

ROLES = ("viewer", "editor", "admin", "contributor", "reporter")
CACHE_SECONDS = 5.0
_READ_ONLY_POSTS = {"/api/markdown/preview"}   # POSTs that change nothing

# What a contributor may call, as (method, path below /p/<project>). Everything else is refused, so a new
# endpoint is closed to contributors until it is listed here. Ownership of an inbox item is checked by the endpoint.
_ITEM = r"[A-Za-z0-9._-]+"
CONTRIBUTOR_ROUTES = (
    ("GET", re.compile(r"/(features|bugs)")),
    ("GET", re.compile(r"/api/about/html")),
    ("GET", re.compile(r"/api/inbox/mine")),
    ("GET", re.compile(rf"/api/inbox/{_ITEM}/screenshot")),
    ("POST", re.compile(r"/api/inbox")),
    ("POST", re.compile(rf"/api/inbox/{_ITEM}/screenshot")),
    ("PATCH", re.compile(rf"/api/inbox/{_ITEM}")),
    ("DELETE", re.compile(rf"/api/inbox/{_ITEM}")),
)

REPORTER_ROUTES = (
    ("GET", re.compile(r"/api/about/html")),
    ("GET", re.compile(r"/api/inbox/mine")),
    ("GET", re.compile(rf"/api/inbox/{_ITEM}/screenshot")),
    ("POST", re.compile(r"/api/inbox")),
    ("POST", re.compile(rf"/api/inbox/{_ITEM}/screenshot")),
)
_ROUTES = {"contributor": CONTRIBUTOR_ROUTES, "reporter": REPORTER_ROUTES}

_cache: dict[tuple[str, str | None], tuple[float, "Verdict"]] = {}


@dataclass(frozen=True)
class Identity:
    email: str
    role: str

    @property
    def name(self) -> str:
        return self.email


@dataclass(frozen=True)
class Verdict:
    status: int                      # 200 allowed, 401 sign in, 403 no access, 503 hub unreachable
    identity: Identity | None = None


def enabled() -> bool:
    return bool(settings.hub_auth_url)


def may_write(identity: Identity) -> bool:
    return identity.role in ("editor", "admin")


def is_contributor(identity: Identity | None) -> bool:
    return identity is not None and identity.role == "contributor"


def is_reporter(identity: Identity | None) -> bool:
    return identity is not None and identity.role == "reporter"


def is_limited(identity: Identity | None) -> bool:
    """Roles confined to a table of routes instead of the read/write split."""
    return identity is not None and identity.role in _ROUTES


def route_allowed(identity: Identity, method: str, tail: str) -> bool:
    """Whether a limited role may make this request; `tail` is the path below /p/<project>."""
    tail = tail.rstrip("/") or "/"
    return any(method == m and pattern.fullmatch(tail) for m, pattern in _ROUTES.get(identity.role, ()))


def is_write(method: str, path: str) -> bool:
    """Whether a request changes anything. Anything but a plain read is a write, except the markdown preview."""
    if method in ("GET", "HEAD", "OPTIONS"):
        return False
    tail = "/api/" + path.split("/api/", 1)[1] if "/api/" in path else path
    return tail not in _READ_ONLY_POSTS


def _auth_url(project: str | None) -> str:
    """The hub's auth URL, asking about one project when the request is for one: the hub answers `contributor`
    for a Power User of that project's app only."""
    if not project:
        return settings.hub_auth_url
    return settings.hub_auth_url + ("&" if "?" in settings.hub_auth_url else "?") + urlencode({"project": project})


def _origin(url: str) -> str:
    """scheme://host[:port] of a URL, lowercased, or '' when it has none."""
    m = re.match(r"^(https?)://([^/?#]+)", url or "", re.IGNORECASE)
    return f"{m.group(1)}://{m.group(2)}".lower() if m else ""


def refuse_cross_site_writes(request: Request) -> None:
    """Dependency of the API router: in hub mode, a write must come from this site's own pages.

    The hub's session cookie is SameSite=Lax, which keeps other websites out but not another page on the same
    site, and not every write endpoint needs a body a form cannot send. So the browser's own statement of where
    a request came from decides: `Sec-Fetch-Site` of `same-origin` (or `none`, a typed address) is let through and
    anything else is refused. A browser too old to send it still sends `Origin` with a POST, which must be this
    server's own origin or the host's (the Renewals proxy hides the public host from this server, so the host's
    home page origin is the other one accepted). A request with neither header is no browser, and carries no
    cookie it did not choose to send."""
    if not enabled() or request.method in ("GET", "HEAD", "OPTIONS"):
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        if site.lower() in ("same-origin", "none"):
            return
        raise HTTPException(status_code=403, detail="Cross-site requests are not accepted.")
    origin = request.headers.get("origin")
    if origin is None:
        return
    if _origin(origin) in {o for o in (_origin(str(request.base_url)), _origin(settings.host_home_url)) if o}:
        return
    raise HTTPException(status_code=403, detail="Cross-site requests are not accepted.")


async def lookup(cookie_header: str | None, project: str | None = None) -> Verdict:
    if not cookie_header:
        return Verdict(401)
    now = time.monotonic()
    cache_key = (cookie_header, project)
    hit = _cache.get(cache_key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(_auth_url(project), headers={"cookie": cookie_header})
    except httpx.HTTPError:
        return Verdict(503)               # fail closed, and do not cache an outage
    if resp.status_code == 200:
        email = resp.headers.get("X-User-Email", "").strip().lower()
        role = resp.headers.get("X-App-Role", "").strip().lower()
        verdict = Verdict(200, Identity(email, role)) if email and role in ROLES else Verdict(403)
    elif resp.status_code in (401, 403):
        verdict = Verdict(resp.status_code)
    else:
        return Verdict(503)
    if len(_cache) > 1000:
        _cache.clear()
    _cache[cache_key] = (now, verdict)
    return verdict
