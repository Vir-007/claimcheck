"""SerpApi client with a content-addressed disk cache.

Every response is written to disk the first time it is fetched, keyed by
engine + request params (never the API key). Lookups check, in order:

1. ``.cache/serpapi/``  - responses fetched on this machine
2. ``fixtures/serpapi/`` - responses committed to the repo for the offline demo

Offline mode never touches the network; a cache miss raises ``CacheMiss``.
A hard cap on live calls protects the (tiny) free-plan credit budget.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

SERPAPI_URL = "https://serpapi.com/search.json"
ACCOUNT_URL = "https://serpapi.com/account.json"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "serpapi"
DEFAULT_FIXTURE_DIR = PROJECT_ROOT / "fixtures" / "serpapi"

# Params that never affect the result and must not leak into cache keys/files.
_IGNORED_PARAMS = {"api_key", "output", "no_cache", "async"}


class CacheMiss(LookupError):
    """Raised in offline mode when a request has no cached response."""


class BudgetExceeded(RuntimeError):
    """Raised when the live-call cap for this process is reached."""


class SerpApiError(RuntimeError):
    pass


def cache_key(params: dict[str, Any]) -> str:
    clean = {k: str(v) for k, v in params.items() if k not in _IGNORED_PARAMS and v is not None}
    blob = json.dumps(clean, sort_keys=True, ensure_ascii=False)
    engine = clean.get("engine", "google")
    return f"{engine}-{hashlib.sha256(blob.encode()).hexdigest()[:16]}"


@dataclass
class SerpClient:
    api_key: str | None = None
    offline: bool = False
    cache_dir: Path = DEFAULT_CACHE_DIR
    fixture_dir: Path | None = DEFAULT_FIXTURE_DIR
    max_live_calls: int = 12
    timeout: float = 75.0
    live_calls: int = 0
    cache_hits: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_env(cls, offline: bool = False, **kw: Any) -> "SerpClient":
        key = os.environ.get("SERPAPI_API_KEY") or None
        if "max_live_calls" not in kw and os.environ.get("CLAIMCHECK_MAX_LIVE_CALLS"):
            kw["max_live_calls"] = int(os.environ["CLAIMCHECK_MAX_LIVE_CALLS"])
        return cls(api_key=key, offline=offline or key is None, **kw)

    # -- cache -----------------------------------------------------------------
    def _paths(self, key: str) -> list[Path]:
        dirs = [self.cache_dir]
        if self.fixture_dir is not None:
            dirs.append(self.fixture_dir)
        return [d / f"{key}.json" for d in dirs]

    def has(self, params: dict[str, Any]) -> bool:
        return any(p.exists() for p in self._paths(cache_key(params)))

    def lookup(self, params: dict[str, Any]) -> dict[str, Any] | None:
        for path in self._paths(cache_key(params)):
            if path.exists():
                with self._lock:
                    self.cache_hits += 1
                return json.loads(path.read_text())["response"]
        return None

    def _store(self, params: dict[str, Any], response: dict[str, Any]) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{cache_key(params)}.json"
        record = {
            "engine": params.get("engine", "google"),
            "params": {k: v for k, v in params.items() if k not in _IGNORED_PARAMS},
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "response": _scrub(response),
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
        return path

    # -- fetch -----------------------------------------------------------------
    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        cached = self.lookup(params)
        if cached is not None:
            return cached
        if self.offline:
            raise CacheMiss(f"no cached response for {cache_key(params)} {params}")
        with self._lock:
            if self.live_calls >= self.max_live_calls:
                raise BudgetExceeded(
                    f"live SerpApi call cap reached ({self.max_live_calls}); "
                    "raise CLAIMCHECK_MAX_LIVE_CALLS to allow more"
                )
            self.live_calls += 1
        resp = httpx.get(SERPAPI_URL, params={**params, "api_key": self.api_key}, timeout=self.timeout)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code != 200 or "error" in data and not _is_empty_result(data):
            raise SerpApiError(f"SerpApi {resp.status_code}: {data.get('error', resp.text[:200])}")
        self._store(params, data)
        return _scrub(data)

    def account(self) -> dict[str, Any]:
        """Account info (free; does not consume a search credit)."""
        if not self.api_key:
            raise SerpApiError("SERPAPI_API_KEY not set")
        data = httpx.get(ACCOUNT_URL, params={"api_key": self.api_key}, timeout=self.timeout).json()
        return {k: v for k, v in data.items() if k != "api_key" and "email" not in k}


def _is_empty_result(data: dict[str, Any]) -> bool:
    # SerpApi returns an "error" field for searches with zero results; that is a
    # valid (cacheable) answer, not a failure.
    return "hasn't returned any results" in str(data.get("error", ""))


def _scrub(data: dict[str, Any]) -> dict[str, Any]:
    """Remove anything key-bearing from a response before it hits disk."""
    data = dict(data)
    meta = dict(data.get("search_metadata") or {})
    for k in list(meta):
        if "json_endpoint" in k or "html" in k.lower():
            meta.pop(k)
    if meta:
        data["search_metadata"] = meta
    params = dict(data.get("search_parameters") or {})
    params.pop("api_key", None)
    if params:
        data["search_parameters"] = params
    data.pop("serpapi_pagination", None)
    data.pop("pagination", None)
    return data
