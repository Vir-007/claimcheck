"""Concurrent multi-engine fan-out over SerpApi."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .queries import SearchTask
from .serp import SerpClient


@dataclass
class TaskResult:
    task: SearchTask
    response: dict[str, Any] | None
    error: str | None
    seconds: float
    from_cache: bool


def run(client: SerpClient, tasks: list[SearchTask], max_workers: int = 6) -> list[TaskResult]:
    def one(task: SearchTask) -> TaskResult:
        t0 = time.perf_counter()
        cached = client.lookup(task.params) is not None
        try:
            resp = client.search(task.params)
            return TaskResult(task, resp, None, time.perf_counter() - t0, cached)
        except Exception as e:  # one engine failing must not sink the verdict
            return TaskResult(task, None, f"{type(e).__name__}: {e}", time.perf_counter() - t0, cached)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(one, tasks))
