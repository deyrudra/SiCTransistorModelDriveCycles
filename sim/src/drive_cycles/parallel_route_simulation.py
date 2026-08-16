from __future__ import annotations

"""
Parallel execution of independent fixed-step candidate-route simulations.

Uses processes, not threads:
- each route gets independent mutable traffic/intersection state
- CPU-bound Python simulation work can use multiple CPU cores
- one slow route cannot block another route's simulation state
"""

from dataclasses import dataclass
from concurrent.futures import (
    ProcessPoolExecutor,
    as_completed,
)
import multiprocessing
import os
from pathlib import Path
from typing import Iterable

from drive_cycles.fast_route_simulation import (
    FastRouteScenario,
    FastRouteResult,
    run_fast_route_scenario,
)


def recommended_worker_count(
    route_count: int,
) -> int:
    """
    Leave one logical CPU free for Windows/Pygame when possible.
    """
    cpu_count = os.cpu_count() or 1

    usable = max(
        1,
        cpu_count - 1,
    )

    return max(
        1,
        min(
            int(route_count),
            usable,
        ),
    )


def run_parallel_scenarios(
    scenarios: Iterable[FastRouteScenario],
    *,
    max_workers: int | None = None,
) -> list[FastRouteResult]:
    items = list(
        scenarios
    )

    if not items:
        return []

    if max_workers is None:
        max_workers = recommended_worker_count(
            len(items)
        )

    # Explicit "spawn" is the safest Windows behavior for Pygame/project state.
    context = multiprocessing.get_context(
        "spawn"
    )

    results = []

    with ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=context,
    ) as executor:
        future_map = {
            executor.submit(
                run_fast_route_scenario,
                scenario,
            ): scenario.route_index
            for scenario in items
        }

        for future in as_completed(
            future_map
        ):
            results.append(
                future.result()
            )

    results.sort(
        key=lambda result: result.route_index
    )

    return results
