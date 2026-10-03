"""End-to-end: claim -> query plan -> multi-engine fan-out -> normalise/dedup ->
stance -> syndication clustering -> verdict."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from . import fanout, queries
from .normalize import dedup, extract
from .serp import PROJECT_ROOT, SerpClient
from .stance import UNRELATED, Backend, make_backend
from .verdict import EngineStat, Evidence, Verdict, Voice, score_claim

DEMO_FILE = PROJECT_ROOT / "fixtures" / "claims.json"


def demo_claims() -> list[dict[str, str]]:
    return json.loads(DEMO_FILE.read_text()) if DEMO_FILE.exists() else []


def load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv()  # also a .env in the current directory, if any


@dataclass
class RunInfo:
    seconds: float
    live_calls: int
    cache_hits: int
    offline: bool


_backend_cache: dict[str, Backend] = {}


def get_backend(kind: str, log: Callable[[str], None]) -> Backend:
    if kind not in _backend_cache:
        _backend_cache[kind] = make_backend(kind, log)
    return _backend_cache[kind]


def check(claim: str, *, offline: bool = False, stance: str = "auto", client: SerpClient | None = None,
          log: Callable[[str], None] = lambda m: None) -> tuple[Verdict, RunInfo]:
    t0 = time.perf_counter()
    client = client or SerpClient.from_env(offline=offline)
    tasks = queries.plan(claim)
    results = fanout.run(client, tasks)
    for r in results:
        if r.error:
            log(f"{r.task.label}: {r.error}")

    raw = extract(results)
    sources = dedup(raw)
    backend = get_backend(stance, log)
    stances = backend.classify(claim, sources)

    engines = engine_stats(results, sources, [st.label != UNRELATED for st in stances])
    verdict = score_claim(claim, sources, stances, engines, backend.name)
    info = RunInfo(round(time.perf_counter() - t0, 2), client.live_calls, client.cache_hits, client.offline)
    return verdict, info


def engine_stats(results: list[fanout.TaskResult], sources, relevant_mask: list[bool]) -> list[EngineStat]:
    stats: dict[str, EngineStat] = {}
    for r in results:
        st = stats.setdefault(r.task.engine, EngineStat(r.task.engine, []))
        st.queries.append(r.task.label.split(":", 1)[1])
        st.cached = st.cached and r.from_cache
        if r.error:
            st.errors.append(r.error)
    for s, rel in zip(sources, relevant_mask):
        for eng in s.engines:
            stats[eng].results += 1
            if rel:
                stats[eng].relevant += 1
                if len(s.engines) == 1:
                    stats[eng].unique_relevant += 1
    return list(stats.values())


# --- serialisation (JSON for the web UI / --json) -----------------------------
def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def evidence_dict(e: Evidence) -> dict[str, Any]:
    s = e.source
    return {
        "title": s.title, "url": s.url, "outlet": s.outlet, "domain": s.domain, "kind": s.kind,
        "published": _iso(s.published), "date_text": s.date_text, "engines": s.engines,
        "queries": sorted({h.task_label for h in s.hits}), "stance": e.stance.label,
        "confidence": e.stance.confidence, "evidence": e.stance.evidence, "cluster": e.cluster_id,
        "wire": s.wire, "cited_by": s.cited_by, "factchecker": s.is_factchecker,
    }


def voice_dict(v: Voice) -> dict[str, Any]:
    return {
        "id": v.id, "stance": v.stance, "weight": v.weight, "rule": v.rule, "size": len(v.items),
        "wire": v.cluster.wire, "outlets": v.outlets, "origin": evidence_dict(v.origin),
        "span_hours": _span_hours(v),
    }


def _span_hours(v: Voice) -> float | None:
    ds = [e.source.published for e in v.items if e.source.published]
    return round((max(ds) - min(ds)).total_seconds() / 3600, 1) if len(ds) > 1 else None


def verdict_dict(v: Verdict, info: RunInfo | None = None) -> dict[str, Any]:
    return {
        "claim": v.claim, "label": v.label, "score": v.score, "summary": v.summary,
        "naive": dict(v.naive), "independent": dict(v.independent), "weighted": v.weighted,
        "earliest": evidence_dict(v.earliest) if v.earliest else None,
        "earliest_support": evidence_dict(v.earliest_support) if v.earliest_support else None,
        "earliest_contradiction": evidence_dict(v.earliest_contradiction) if v.earliest_contradiction else None,
        "syndicated": [voice_dict(x) for x in v.syndicated],
        "dissent": [voice_dict(x) for x in v.dissent],
        "voices": len(v.voices),
        "evidence": [evidence_dict(e) for e in v.evidence],
        "engines": [e.__dict__ for e in v.engines],
        "stance_method": v.stance_method, "total_sources": v.total_sources,
        "run": info.__dict__ if info else None,
    }
