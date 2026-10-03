"""Minimal web UI: FastAPI + one static HTML page.  Run: `claimcheck-web`."""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .pipeline import check, demo_claims, get_backend, load_env, verdict_dict
from .serp import BudgetExceeded, SerpClient

STATIC = Path(__file__).parent / "static"

load_env()
app = FastAPI(title="claimcheck", docs_url="/api/docs")
_live_client: SerpClient | None = None
_lock = threading.Lock()


def live_client() -> SerpClient:
    # One shared client so the live-call budget is enforced across requests.
    global _live_client
    with _lock:
        if _live_client is None:
            _live_client = SerpClient.from_env()
        return _live_client


class CheckRequest(BaseModel):
    claim: str = Field(min_length=8, max_length=400)
    offline: bool = False
    stance: str = "auto"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    c = live_client()
    return {"demos": demo_claims(), "has_key": bool(c.api_key), "live_calls": c.live_calls,
            "max_live_calls": c.max_live_calls}


@app.post("/api/check")
def api_check(req: CheckRequest) -> dict:
    client = SerpClient.from_env(offline=True) if req.offline else live_client()
    try:
        verdict, info = check(req.claim.strip(), client=client, stance=req.stance)
    except BudgetExceeded as e:
        raise HTTPException(429, str(e))
    if verdict.total_sources == 0:
        msg = ("No cached results for this claim. Offline mode only covers the demo claims."
               if client.offline else "No results came back from SerpApi (see server log).")
        raise HTTPException(404, msg)
    return verdict_dict(verdict, info)


def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser(prog="claimcheck-web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    # Load the stance model before the first request so the demo is snappy.
    get_backend("auto", print)
    print(f"claimcheck web UI on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
