"""Identity and roles from the RevOps hub.

In hub mode (`hub_auth_url` set) the only identity this app accepts is the
hub's own answer to `GET /internal/auth?app=strategy`, asked by this server with
the browser's session cookie forwarded. Headers a client sends for identity
(`X-Host-User`, `X-User-Email`, `X-App-Role`) are ignored, so nothing depends on
a proxy stripping them.

Roles: viewer reads; editor reads and writes (edits, submissions, triage);
admin also manages projects and the manifest (reserved, nothing needs it yet).
The hub is asked on every request, with a few seconds of caching per cookie, so
a revoked grant stops working within the TTL. Any hub failure denies access.
"""
from __future__ import annotations
import time
from dataclasses import dataclass

import httpx

from .config import settings

ROLES = ("viewer", "editor", "admin")
CACHE_SECONDS = 5.0
_READ_ONLY_POSTS = {"/api/markdown/preview"}   # POSTs that change nothing

_cache: dict[str, tuple[float, "Verdict"]] = {}


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


def is_write(method: str, path: str) -> bool:
    """Whether a request changes anything. Anything but a plain read is a write, except the markdown preview."""
    if method in ("GET", "HEAD", "OPTIONS"):
        return False
    tail = "/api/" + path.split("/api/", 1)[1] if "/api/" in path else path
    return tail not in _READ_ONLY_POSTS


async def lookup(cookie_header: str | None) -> Verdict:
    if not cookie_header:
        return Verdict(401)
    now = time.monotonic()
    hit = _cache.get(cookie_header)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(settings.hub_auth_url, headers={"cookie": cookie_header})
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
    _cache[cookie_header] = (now, verdict)
    return verdict
