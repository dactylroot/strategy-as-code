import hashlib
import hmac
import json

import pytest

from app import poller, scope
from app.registry import run_git
from tests.test_project_scope import program, origin_of  # noqa: F401
from tests.test_registry_writer import _git, clone_of


def push_elsewhere(program, tmp_path, key="alpha", name="other"):
    other = clone_of(tmp_path, origin_of(program, key), name)
    (other / "NEW.txt").write_text("x")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "outside")
    _git(other, "push", "-q", "origin", "main")
    return run_git(other, ["rev-parse", "HEAD"])


def test_refresh_all_isolates_failures(program):
    res = poller.refresh_all(program)
    assert res == {"alpha": None, "beta": None}
    bad = type(program.get("beta").spec)(key="bad", repo="/nonexistent/x.git")
    from app.registry import Registry
    reg = Registry([program.get("alpha").spec, bad], program.cache_dir)
    out = poller.refresh_all(reg)
    assert out["alpha"] is None and out["bad"]


def test_poll_picks_up_a_push_made_elsewhere(program, tmp_path):
    poller.refresh_all(program)
    sha = push_elsewhere(program, tmp_path)
    assert program.get("alpha").status.sha != sha
    poller.refresh_all(program)
    assert program.get("alpha").status.sha == sha


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature_check():
    assert poller.valid_signature("s", b"x", sign("s", b"x"))
    assert not poller.valid_signature("s", b"x", sign("t", b"x"))
    assert not poller.valid_signature("s", b"x", None) and not poller.valid_signature("", b"x", sign("", b"x"))


def test_webhook_refreshes_only_the_named_project(client, program, tmp_path, monkeypatch):
    from app.config import _base
    monkeypatch.setattr(_base, "webhook_secret", "topsecret")
    poller.refresh_all(program)
    sha = push_elsewhere(program, tmp_path)
    repo = str(origin_of(program, "alpha"))
    body = json.dumps({"repository": {"full_name": "org/alpha", "clone_url": repo}}).encode()
    assert client.post("/hooks/refresh", content=body).status_code == 401
    r = client.post("/hooks/refresh", content=body, headers={"X-Hub-Signature-256": sign("topsecret", body)})
    assert r.status_code == 200 and r.json()["refreshed"] == ["alpha"]
    assert program.get("alpha").status.sha == sha


def test_webhook_is_disabled_without_a_secret(client, program):
    assert client.post("/hooks/refresh", content=b"{}").status_code == 404
