"""
Single-user authentication backed by a figtion config file.

Default config path: ~/.config/strategy-as-code/auth.yml
Override via AUTH_CONFIG environment variable.

Example auth.yml:
    enabled: true
    username: admin
    password: changeme
"""
from __future__ import annotations
import hashlib
import hmac
import os
import secrets
from pathlib import Path

import figtion

COOKIE_NAME = "pac_auth"
_CONFIG_PATH = Path(os.environ.get(
    "AUTH_CONFIG",
    Path.home() / ".config" / "strategy-as-code" / "auth.yml",
))

_defaults = {
    "enabled":    False,
    "username":   "admin",
    "password":   "changeme",
    "secret_key": "",
}

_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
cfg = figtion.Config(filepath=str(_CONFIG_PATH), defaults=_defaults, verbose=False)

# Auto-generate a stable secret key on first run
if not cfg["secret_key"]:
    cfg["secret_key"] = secrets.token_hex(32)
    cfg.dump()


def enabled() -> bool:
    return bool(cfg["enabled"])


def check_credentials(username: str, password: str) -> bool:
    return (
        secrets.compare_digest(username, cfg["username"])
        and secrets.compare_digest(password, cfg["password"])
    )


def _sign(username: str) -> str:
    key = cfg["secret_key"].encode()
    return hmac.new(key, username.encode(), hashlib.sha256).hexdigest()


def make_cookie(username: str) -> str:
    return f"{username}:{_sign(username)}"


def verify_cookie(value: str) -> str | None:
    try:
        username, sig = value.rsplit(":", 1)
        if hmac.compare_digest(sig, _sign(username)):
            return username
    except Exception:
        pass
    return None


def is_authenticated(request) -> bool:
    if not enabled():
        return True
    token = request.cookies.get(COOKIE_NAME)
    return bool(token and verify_cookie(token))
