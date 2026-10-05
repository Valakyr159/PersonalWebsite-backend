"""
Persistence for the Genshin guide: the current meta snapshot and the daily usage counters.

Render's free tier spins the service down after 15 idle minutes and wipes memory and disk, so anything
kept in the process is lost on every wake-up or deploy. When SUPABASE_URL, SUPABASE_ANON_KEY and
GENSHIN_DB_SECRET are set, the snapshot and counters live in Supabase (see db/genshin_meta.sql);
otherwise, and whenever Supabase can't be reached, a per-process memory store takes over so the guide keeps
working (it just regenerates the meta more often).

Least privilege: the Supabase project is SHARED with other apps (it holds real data), so this backend does NOT
use the service_role key. It uses the public anon key, which can do nothing by itself (no table has policies):
the only thing it can call is a handful of `genshin_*` SECURITY DEFINER functions, and each one refuses to run
unless it receives GENSHIN_DB_SECRET, whose hash lives in the database. If this backend leaked its environment,
an attacker could only tamper with the Genshin meta and counters, never read or change the other apps' data.
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)


@dataclass
class Snapshot:
    payload: dict[str, Any]
    generated_at: float  # epoch seconds
    degraded: bool = False


class Store(Protocol):
    async def load(self) -> Snapshot | None: ...
    async def save(self, snapshot: Snapshot) -> None: ...
    async def bump(self, kind: str, limit: int) -> bool: ...


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class MemoryStore:
    """Per-process store. Lost on restart: fine as a fallback, not as the source of truth."""

    def __init__(self) -> None:
        self._snapshot: Snapshot | None = None
        self._usage: dict[tuple[str, str], int] = {}

    async def load(self) -> Snapshot | None:
        return self._snapshot

    async def save(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot

    async def bump(self, kind: str, limit: int) -> bool:
        """Atomically (single event loop) counts one use of `kind` today; False once `limit` is reached."""
        key = (_today(), kind)
        if self._usage.get(key, 0) >= limit:
            return False
        self._usage[key] = self._usage.get(key, 0) + 1
        return True


class SupabaseStore:
    """Calls the genshin_* functions through PostgREST's /rpc endpoint with plain httpx (no extra dependency)."""

    def __init__(self, url: str, anon_key: str, secret: str, timeout: float = 8) -> None:
        self.base = url.rstrip("/") + "/rest/v1/rpc"
        self.timeout = timeout
        self._secret = secret
        self._headers = {"apikey": anon_key, "Authorization": f"Bearer {anon_key}", "Content-Type": "application/json"}

    async def _rpc(self, function: str, args: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base}/{function}", headers=self._headers,
                                         json={"p_secret": self._secret, **args})
        response.raise_for_status()
        return response.json() if response.content else None

    async def load(self) -> Snapshot | None:
        row = await self._rpc("genshin_get_meta", {})
        if not row:
            return None
        payload = row["payload"]
        return Snapshot(payload=payload, generated_at=float(payload["fetchedAt"]), degraded=bool(row["degraded"]))

    async def save(self, snapshot: Snapshot) -> None:
        await self._rpc("genshin_save_meta", {"p_payload": snapshot.payload, "p_model": str(snapshot.payload.get("model", "")),
                                              "p_degraded": snapshot.degraded})

    async def bump(self, kind: str, limit: int) -> bool:
        return bool(await self._rpc("genshin_bump_usage", {"p_kind": kind, "p_limit": limit}))


class ResilientStore:
    """Supabase first; any failure falls back to memory so a database hiccup never takes the guide down."""

    def __init__(self, primary: Store, fallback: Store | None = None) -> None:
        self.primary, self.fallback = primary, fallback or MemoryStore()

    async def load(self) -> Snapshot | None:
        remembered = await self.fallback.load()
        try:
            stored = await self.primary.load()
        except Exception as exc:
            _log_failure("load", exc)
            return remembered
        # Both can exist after an outage: prefer the newer one.
        candidates = [s for s in (stored, remembered) if s]
        return max(candidates, key=lambda s: s.generated_at) if candidates else None

    async def save(self, snapshot: Snapshot) -> None:
        await self.fallback.save(snapshot)
        try:
            await self.primary.save(snapshot)
        except Exception as exc:
            _log_failure("save", exc)

    async def bump(self, kind: str, limit: int) -> bool:
        try:
            return await self.primary.bump(kind, limit)
        except Exception as exc:
            _log_failure("bump", exc)
            return await self.fallback.bump(kind, limit)


def _log_failure(action: str, exc: Exception) -> None:
    # Status code or exception type only: an httpx message can echo the request URL.
    detail = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
    log.warning("supabase %s failed, using memory: %s", action, detail)


def make_store() -> Store:
    url, anon_key, secret = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"), os.getenv("GENSHIN_DB_SECRET")
    if url and anon_key and secret:
        return ResilientStore(SupabaseStore(url, anon_key, secret))
    log.info("SUPABASE_URL / SUPABASE_ANON_KEY / GENSHIN_DB_SECRET not all set: Genshin meta is kept in memory only")
    return MemoryStore()

