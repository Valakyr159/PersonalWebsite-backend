"""Genshin store: memory counters, the scoped Supabase RPC contract (mocked) and the memory fallback."""
import asyncio
import json

import httpx
import pytest

from src.mcp_server import genshin_store as gs
from src.mcp_server.genshin_store import MemoryStore, ResilientStore, Snapshot, SupabaseStore

ANON = "anon-key-abc"
SECRET = "app-secret-xyz"
URL = "https://abc.supabase.co"


def run(coro):
    return asyncio.run(coro)


def supabase_with(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(gs.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return SupabaseStore(URL, ANON, SECRET)


class Exploding:
    """Primary store that always fails, like an unreachable or paused Supabase project."""
    async def load(self): raise httpx.ConnectError("down")
    async def save(self, s): raise httpx.ConnectError("down")
    async def bump(self, kind, limit): raise httpx.ConnectError("down")


def snap(at, degraded=False, **extra):
    return Snapshot(payload={"fetchedAt": at, "patch": "7.1", **extra}, generated_at=at, degraded=degraded)


# ---------- MemoryStore ----------

def test_memory_store_roundtrips_a_snapshot():
    store = MemoryStore()
    assert run(store.load()) is None
    run(store.save(snap(100)))
    assert run(store.load()).generated_at == 100


def test_memory_store_caps_each_kind_separately():
    store = MemoryStore()
    results = [run(store.bump("chat", 2)) for _ in range(3)]
    assert results == [True, True, False]
    assert run(store.bump("meta", 1)) is True  # its own counter
    assert run(store.bump("meta", 1)) is False


# ---------- SupabaseStore (mocked REST) ----------

def test_every_call_goes_through_a_genshin_function_with_the_secret_in_the_body_not_the_url(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.headers, json.loads(request.content)))
        return httpx.Response(200, json=None)

    store = supabase_with(monkeypatch, handler)
    run(store.load())
    run(store.save(snap(5, model="m")))
    run(store.bump("chat", 10))

    assert [(m, u) for m, u, _, _ in seen] == [
        ("POST", f"{URL}/rest/v1/rpc/genshin_get_meta"),
        ("POST", f"{URL}/rest/v1/rpc/genshin_save_meta"),
        ("POST", f"{URL}/rest/v1/rpc/genshin_bump_usage"),
    ]
    for _, url, headers, body in seen:
        assert body["p_secret"] == SECRET                       # authenticates every call
        assert SECRET not in url and ANON not in url            # never in the URL / access logs
        assert headers["apikey"] == ANON and headers["authorization"] == f"Bearer {ANON}"


def test_supabase_never_touches_tables_directly(monkeypatch):
    """Least privilege: the anon key has no table grants, so any /rest/v1/<table> call would be a bug."""
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, json=None)

    store = supabase_with(monkeypatch, handler)
    run(store.load()); run(store.save(snap(1))); run(store.bump("meta", 1))
    assert all("/rest/v1/rpc/genshin_" in u for u in urls)


def test_supabase_load_returns_a_snapshot(monkeypatch):
    row = {"payload": {"fetchedAt": 1234, "patch": "7.1"}, "degraded": True}
    loaded = run(supabase_with(monkeypatch, lambda r: httpx.Response(200, json=row)).load())
    assert loaded.generated_at == 1234 and loaded.degraded is True and loaded.payload["patch"] == "7.1"


def test_supabase_load_returns_none_when_there_is_no_row(monkeypatch):
    assert run(supabase_with(monkeypatch, lambda r: httpx.Response(200, json=None)).load()) is None


def test_supabase_save_sends_the_snapshot_fields(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(204)  # a void function answers with no body

    run(supabase_with(monkeypatch, handler).save(snap(55, degraded=True, model="m")))
    assert seen["p_payload"]["fetchedAt"] == 55 and seen["p_model"] == "m" and seen["p_degraded"] is True


def test_supabase_bump_passes_kind_and_limit_and_returns_the_decision(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=len(bodies) < 2)  # allowed once, then capped

    store = supabase_with(monkeypatch, handler)
    assert run(store.bump("chat", 300)) is True and run(store.bump("chat", 300)) is False
    assert bodies[0] == {"p_secret": SECRET, "p_kind": "chat", "p_limit": 300}


def test_a_wrong_secret_is_an_error_that_the_resilient_layer_absorbs(monkeypatch):
    # The database answers 403/42501 ("forbidden") when the secret doesn't match its stored hash.
    store = supabase_with(monkeypatch, lambda r: httpx.Response(403, json={"code": "42501", "message": "forbidden"}))
    with pytest.raises(httpx.HTTPStatusError):
        run(store.load())
    assert run(ResilientStore(store).bump("chat", 1)) is True  # falls back to memory instead of breaking the chat


# ---------- ResilientStore ----------

def test_resilient_store_serves_from_memory_when_supabase_is_down():
    store = ResilientStore(Exploding())
    run(store.save(snap(10)))  # must not raise
    assert run(store.load()).generated_at == 10  # survived in the fallback
    assert [run(store.bump("chat", 1)) for _ in range(2)] == [True, False]  # caps still enforced


def test_resilient_store_save_writes_to_both():
    primary = MemoryStore()
    store = ResilientStore(primary)
    run(store.save(snap(7)))
    assert run(primary.load()).generated_at == 7


def test_resilient_store_prefers_the_newer_snapshot_after_an_outage():
    primary, fallback = MemoryStore(), MemoryStore()
    run(primary.save(snap(100)))
    run(fallback.save(snap(200)))  # saved while the primary was down
    assert run(ResilientStore(primary, fallback).load()).generated_at == 200


def test_resilient_store_log_never_contains_the_secret_the_key_or_the_url(monkeypatch, caplog):
    store = ResilientStore(supabase_with(monkeypatch, lambda r: httpx.Response(401)))
    with caplog.at_level("WARNING"):
        run(store.load())
    assert "supabase load failed" in caplog.text
    assert SECRET not in caplog.text and ANON not in caplog.text and "supabase.co" not in caplog.text


# ---------- make_store ----------

def test_make_store_uses_supabase_only_when_all_three_variables_are_set(monkeypatch):
    names = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "GENSHIN_DB_SECRET")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    assert isinstance(gs.make_store(), MemoryStore)
    for name, value in zip(names, (URL, ANON, SECRET)):
        monkeypatch.setenv(name, value)
        expected = ResilientStore if name == names[-1] else MemoryStore  # a partial config must not half-work
        assert isinstance(gs.make_store(), expected), name


def test_the_service_role_key_is_not_a_supported_configuration(monkeypatch):
    """The project is shared with other apps: setting a service key must not switch Supabase on by itself."""
    for name in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "GENSHIN_DB_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-role-should-be-ignored")
    assert isinstance(gs.make_store(), MemoryStore)
