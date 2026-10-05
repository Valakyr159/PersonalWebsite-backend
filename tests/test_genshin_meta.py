"""MetaService: the meta is generated on demand, once, in the background, and never when a fresh snapshot exists."""
import asyncio
import os

os.environ.setdefault("GROQ_API_KEY", "test-key")

import httpx
import pytest
from starlette.testclient import TestClient

from src.mcp_server import genshin, server
from src.mcp_server.genshin import MetaService
from src.mcp_server.genshin_store import MemoryStore, Snapshot

HOUR = 3600


@pytest.fixture(autouse=True)
def clean_state():
    """The store, service and limiters are module globals: every test starts from a clean slate."""
    genshin.store = MemoryStore()
    genshin.meta_service = MetaService(genshin.store, genshin.fetch_meta)
    for limiter in (genshin.status_limiter, genshin.chat_limiter, genshin.profile_limiter):
        limiter._hits.clear()
    yield


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class SlowStore(MemoryStore):
    """Yields to the event loop on every call, like a real network round trip to Supabase would."""

    async def load(self):
        await asyncio.sleep(0)
        return await super().load()

    async def save(self, snapshot):
        await asyncio.sleep(0)
        await super().save(snapshot)

    async def bump(self, kind, limit):
        await asyncio.sleep(0)
        return await super().bump(kind, limit)


class Harness:
    """A service with a fake clock and a generator whose result, failure and speed the test controls."""

    def __init__(self, store=None, degraded=False, fail=None):
        self.clock, self.store = Clock(), store or MemoryStore()
        self.calls, self.degraded, self.fail = 0, degraded, fail
        self.gate = None  # set to an asyncio.Event to hold the generation open
        self.service = MetaService(self.store, self.generate, clock=self.clock)

    async def generate(self):
        self.calls += 1
        if self.gate:
            await self.gate.wait()
        if self.fail:
            raise self.fail
        return {"patch": "7.1", "teams": [], "characters": [], "banners": [1], "sources": [], "model": "m",
                "degraded": self.degraded, "fetchedAt": int(self.clock())}

    async def finish(self):
        await self.service._task


def seeded(store, age, degraded=False, patch="7.0"):
    clock_now = 1_000_000.0
    run(store.save(Snapshot({"patch": patch, "fetchedAt": clock_now - age, "teams": []}, clock_now - age, degraded)))


def run(coro):
    return asyncio.run(coro)


def test_a_fresh_snapshot_is_served_without_any_ai_call():
    h = Harness()
    seeded(h.store, age=HOUR)

    async def go():
        return await h.service.ensure(), await h.service.ensure()

    first, second = run(go())
    assert first["status"] == second["status"] == "fresh" and first["meta"]["patch"] == "7.0"
    assert h.calls == 0


def test_empty_store_starts_one_generation_and_then_serves_it_fresh():
    h = Harness()

    async def go():
        started = await h.service.ensure()
        await h.finish()
        return started, await h.service.ensure()

    started, done = run(go())
    assert started["status"] == "updating" and started["meta"] is None and started["elapsed"] == 0
    assert done["status"] == "fresh" and done["meta"]["banners"] == [1]
    assert h.calls == 1
    assert run(h.store.load()).payload["patch"] == "7.1"  # persisted


def test_concurrent_requests_start_exactly_one_generation():
    h = Harness(store=SlowStore())  # with a store that really suspends, requests interleave before the lock

    async def go():
        h.gate = asyncio.Event()
        states = await asyncio.gather(*[h.service.ensure() for _ in range(8)])
        h.gate.set()
        await h.finish()
        return states

    states = run(go())
    assert {s["status"] for s in states} == {"updating"}
    assert h.calls == 1


def test_while_updating_the_previous_snapshot_is_shown_with_a_growing_timer():
    h = Harness()
    seeded(h.store, age=13 * HOUR)  # expired

    async def go():
        h.gate = asyncio.Event()
        first = await h.service.ensure()
        h.clock.t += 7
        second = await h.service.ensure()
        h.gate.set()
        await h.finish()
        return first, second

    first, second = run(go())
    assert first["status"] == second["status"] == "updating"
    assert second["meta"]["patch"] == "7.0"  # stale-while-revalidate: old data stays visible
    assert (first["elapsed"], second["elapsed"]) == (0, 7)


def test_expired_snapshot_is_regenerated_on_demand():
    h = Harness()
    seeded(h.store, age=13 * HOUR)

    async def go():
        await h.service.ensure()
        await h.finish()
        return await h.service.ensure()

    state = run(go())
    assert state["status"] == "fresh" and state["meta"]["patch"] == "7.1" and h.calls == 1


def test_a_snapshot_survives_a_restart_because_it_lives_in_the_store():
    first_life = Harness()

    async def generate_once():
        await first_life.service.ensure()
        await first_life.finish()

    run(generate_once())
    assert first_life.calls == 1

    reborn = Harness(store=first_life.store)  # a new process (Render woke up) with the same database
    state = run(reborn.service.ensure())
    assert state["status"] == "fresh" and reborn.calls == 0  # no AI call after the restart


def test_failure_sets_a_cooldown_instead_of_retrying_on_every_poll():
    h = Harness(fail=httpx.ConnectError("down"))

    async def go():
        await h.service.ensure()
        await h.finish()
        during = [await h.service.ensure() for _ in range(5)]  # polls inside the cooldown
        h.clock.t += genshin.META_FAIL_COOLDOWN_SECONDS + 1
        await h.service.ensure()  # cooldown over: tries again
        await h.finish()
        return during

    during = run(go())
    assert {s["status"] for s in during} == {"unavailable"} and during[0]["meta"] is None
    assert "reintentará" in during[0]["message"]
    assert h.calls == 2  # the first try and the one after the cooldown, nothing in between


def test_failure_with_an_old_snapshot_reports_stale_and_keeps_showing_it():
    h = Harness(fail=httpx.ConnectError("down"))
    seeded(h.store, age=13 * HOUR)

    async def go():
        await h.service.ensure()
        await h.finish()
        return await h.service.ensure()

    state = run(go())
    assert state["status"] == "stale" and state["meta"]["patch"] == "7.0" and state["message"]


def test_daily_cap_blocks_new_generations(monkeypatch):
    monkeypatch.setattr(genshin, "META_DAILY_CAP", 1)
    h = Harness()

    async def go():
        await h.service.ensure()
        await h.finish()
        h.clock.t += 13 * HOUR  # expired again, but the cap is spent
        return await h.service.ensure()

    state = run(go())
    assert state["status"] == "stale" and "límite diario" in state["message"] and h.calls == 1


def test_degraded_result_is_kept_when_nothing_better_exists_and_expires_fast():
    h = Harness(degraded=True)

    async def go():
        await h.service.ensure()
        await h.finish()
        fresh = await h.service.ensure()
        h.clock.t += genshin.META_DEGRADED_TTL_SECONDS + 1
        h.degraded = False
        again = await h.service.ensure()  # expired + cooldown over: the primary model gets another chance
        await h.finish()
        return fresh, again, await h.service.ensure()

    fresh, again, final = run(go())
    assert fresh["status"] == "fresh" and fresh["meta"]["degraded"] is True
    assert again["status"] == "updating"
    assert final["meta"]["degraded"] is False and h.calls == 2


def test_degraded_result_never_replaces_an_existing_good_snapshot():
    h = Harness(degraded=True)
    seeded(h.store, age=13 * HOUR, patch="7.0")

    async def go():
        await h.service.ensure()
        await h.finish()
        return await h.service.ensure()

    state = run(go())
    assert state["status"] == "stale" and state["meta"]["patch"] == "7.0" and state["meta"]["degraded"] is False
    assert run(h.store.load()).payload["patch"] == "7.0"  # the database still holds the good one


def test_another_instance_writing_the_store_is_picked_up_after_the_refresh_window():
    h = Harness()
    seeded(h.store, age=13 * HOUR, patch="old")

    async def go():
        await h.service.ensure()  # starts generation; remember the old snapshot in memory
        await h.finish()
        h.gate = asyncio.Event()  # next generation would hang: it must not be needed
        await h.store.save(Snapshot({"patch": "newer", "fetchedAt": h.clock() + 1}, h.clock() + 1))
        h.clock.t += genshin.SNAPSHOT_REFRESH_SECONDS + 1
        return await h.service.ensure()

    assert run(go())["meta"]["patch"] == "newer"


# ---------- HTTP layer ----------

def test_endpoint_serves_a_fresh_snapshot_without_calling_gemini(monkeypatch):
    seeded(genshin.store, age=HOUR)
    genshin.meta_service = MetaService(genshin.store, lambda: pytest.fail("AI must not be called"), clock=Clock())
    with TestClient(server.starlette_app) as client:
        body = client.post("/genshin/meta").json()
    assert body["status"] == "fresh" and body["meta"]["patch"] == "7.0"


def test_endpoint_goes_from_updating_to_fresh_by_polling():
    gate = {}

    async def slow_generate():
        await gate["event"].wait()
        return {"patch": "7.1", "teams": [], "characters": [], "banners": [], "sources": [], "model": "m",
                "degraded": False, "fetchedAt": int(Clock()())}

    genshin.meta_service = MetaService(genshin.store, slow_generate, clock=Clock())
    with TestClient(server.starlette_app) as client:
        async def arm():
            gate["event"] = asyncio.Event()
        client.portal.call(arm)  # same loop the app runs on
        first = client.post("/genshin/meta").json()
        assert first["status"] == "updating" and first["meta"] is None
        assert client.post("/genshin/meta").json()["status"] == "updating"  # polling doesn't start a second one
        client.portal.call(gate["event"].set)
        for _ in range(100):
            body = client.post("/genshin/meta").json()
            if body["status"] != "updating":
                break
            client.portal.call(asyncio.sleep, 0.01)
    assert body["status"] == "fresh" and body["meta"]["patch"] == "7.1"


def test_endpoint_returns_503_when_there_is_nothing_to_show_and_generation_failed():
    async def broken():
        raise RuntimeError("GEMINI_API_KEY is not configured")

    genshin.meta_service = MetaService(genshin.store, broken, clock=Clock())
    with TestClient(server.starlette_app) as client:
        client.post("/genshin/meta")
        client.portal.call(asyncio.sleep, 0.05)
        response = client.post("/genshin/meta")
    assert response.status_code == 503 and response.json()["status"] == "unavailable"


def test_polling_is_not_throttled_like_an_ai_call_until_the_polling_limit():
    seeded(genshin.store, age=HOUR)
    genshin.meta_service = MetaService(genshin.store, lambda: pytest.fail("AI must not be called"), clock=Clock())
    client = TestClient(server.starlette_app)
    codes = [client.post("/genshin/meta").status_code for _ in range(genshin.status_limiter.limit + 1)]
    assert codes[:-1] == [200] * genshin.status_limiter.limit and codes[-1] == 429


def test_meta_endpoint_no_longer_accepts_a_forced_refresh():
    seeded(genshin.store, age=HOUR)
    genshin.meta_service = MetaService(genshin.store, lambda: pytest.fail("refresh must not call the AI"), clock=Clock())
    assert TestClient(server.starlette_app).post("/genshin/meta?refresh=1").json()["status"] == "fresh"


# ---------- models and quota ----------

def test_default_models_are_the_two_cheap_lite_ones(monkeypatch):
    monkeypatch.delenv("GEMINI_META_MODELS", raising=False)
    monkeypatch.delenv("GEMINI_CHAT_MODELS", raising=False)
    assert genshin.models_from_env("GEMINI_META_MODELS") == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    assert genshin.models_from_env("GEMINI_CHAT_MODELS") == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]


def test_models_env_is_comma_separated_and_trimmed(monkeypatch):
    monkeypatch.setenv("GEMINI_META_MODELS", " a , b ,")
    assert genshin.models_from_env("GEMINI_META_MODELS") == ["a", "b"]


def _status_error(code, text=""):
    request = httpx.Request("POST", "https://x")
    return httpx.HTTPStatusError("e", request=request, response=httpx.Response(code, text=text, request=request))


def test_a_daily_quota_429_is_not_retried_but_a_per_minute_one_is():
    assert genshin._transient(_status_error(503)) is True
    assert genshin._transient(_status_error(429, "RPM exceeded")) is True
    assert genshin._transient(_status_error(429, "GenerateRequestsPerDayPerProjectPerModel-FreeTier")) is False
    assert genshin._transient(_status_error(404)) is False
    assert genshin._transient(httpx.ReadTimeout("slow")) is True


# ---------- chat daily cap ----------

def _chat_body():
    return {"messages": [{"role": "user", "content": "hola"}], "context": "ctx"}


def test_chat_daily_cap_blocks_with_a_clear_message_and_no_gemini_call(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(genshin, "CHAT_DAILY_CAP", 2)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=b'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}\n\n')

    real = httpx.AsyncClient
    monkeypatch.setattr(genshin.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    client = TestClient(server.starlette_app)
    codes = [client.post("/genshin/chat", json=_chat_body()).status_code for _ in range(3)]
    blocked = client.post("/genshin/chat", json=_chat_body())

    assert codes == [200, 200, 429]
    assert blocked.json()["error"] == "daily_limit" and "límite diario" in blocked.json()["message"]
    assert len(calls) == 2  # the blocked requests never reached Gemini


def test_chat_cap_is_shared_by_everyone_not_per_ip(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(genshin, "CHAT_DAILY_CAP", 1)
    real = httpx.AsyncClient
    monkeypatch.setattr(genshin.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=b'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}\n\n')), **kw))
    client = TestClient(server.starlette_app)
    assert client.post("/genshin/chat", json=_chat_body(), headers={"x-forwarded-for": "1.1.1.1"}).status_code == 200
    assert client.post("/genshin/chat", json=_chat_body(), headers={"x-forwarded-for": "2.2.2.2"}).status_code == 429


def test_invalid_chat_requests_do_not_spend_the_daily_cap(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(genshin, "CHAT_DAILY_CAP", 1)
    client = TestClient(server.starlette_app)
    assert client.post("/genshin/chat", json={"messages": []}).status_code == 400
    assert run(genshin.store.bump("chat", 1)) is True  # the cap is still untouched
