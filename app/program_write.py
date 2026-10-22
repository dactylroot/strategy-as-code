"""Route a web write endpoint through the shared git writer in program mode.

Decorate an endpoint's function with `@program_write("message")`. Unscoped, the
endpoint runs exactly as before. Scoped to a member project (`/p/<key>/...`), the
whole function body runs inside `writer.edit`: the clone has just been synced to
the remote tip, so the endpoint reads current files, applies its usual transform
and writes them; the writer then commits as the user and pushes, retrying the
function on a fresh checkout if the push loses a race.
"""
from __future__ import annotations
import functools
from typing import Callable

from fastapi import HTTPException, Request

from . import hub_identity, scope
from .writer import Actor, EditAborted, edit


def request_email(request: Request | None) -> str:
    """The signed-in person's email. In hub mode this is only ever the hub's verdict;
    otherwise the host app's forwarded X-Host-User."""
    if request is None:
        return ""
    if hub_identity.enabled():
        identity = getattr(request.state, "identity", None)
        return identity.email if identity else ""
    return (request.headers.get("X-Host-User") or "").strip()


def request_actor(request: Request | None) -> Actor | None:
    """Who is making the request, from the host app's forwarded identity (the same
    X-Host-User the legacy owner default trusts). None = attribute to the service."""
    who = request_email(request)
    if not who:
        return None
    return Actor(who, who if "@" in who else f"{who}@strategy-as-code")


def refuse_when_scoped(what: str) -> Callable[[Request], None]:
    """Dependency for endpoints with no program-mode equivalent yet."""
    def dep(request: Request) -> None:
        if scope.scope_key():
            raise HTTPException(status_code=403, detail=f"{what} is not available for program projects yet.")
    return dep


def program_write(message: str | Callable[[dict], str]) -> Callable:
    """`message` is a format string over the endpoint's simple arguments, or a
    callable taking those arguments (including request bodies) as a dict."""
    def decorate(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = scope.scope_key()
            registry = scope.get_registry()
            if key is None or registry is None:
                return fn(*args, **kwargs)

            out: dict = {}

            def run(_checkout) -> None:
                token = scope.writing.set(True)
                try:
                    out["value"] = fn(*args, **kwargs)
                except Exception as exc:  # incl. HTTPException: abandon the write, report it as-is
                    raise EditAborted() from exc
                finally:
                    scope.writing.reset(token)

            try:
                if callable(message):
                    text = message(kwargs)
                else:
                    text = message.format(**{k: v for k, v in kwargs.items() if isinstance(v, (str, int))})
            except (KeyError, IndexError, ValueError, AttributeError):
                text = message if isinstance(message, str) else "inbox update"
            try:
                edit(registry, key, run, f"UI: {text}", request_actor(kwargs.get("request")))
            except EditAborted as aborted:
                raise aborted.__cause__ from None
            return out["value"]
        return wrapper
    return decorate
