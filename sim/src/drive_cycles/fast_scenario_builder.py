from __future__ import annotations

"""
Helpers for creating fast-simulation worker scenarios from routes currently
loaded in the Pygame visualizer.

No project graph object is pickled. Instead, each worker receives:
- route node IDs
- explicit cached OSM chunk filenames
- configuration/cache paths

The worker then reconstructs its own independent RoadNetwork.
"""

from pathlib import Path
from typing import Iterable

from chunks.grid import (
    CHUNK_SIZE,
    world_to_chunk,
)

from drive_cycles.fast_route_simulation import (
    FastRouteScenario,
)


def route_chunk_keys(
    network,
    route,
    *,
    halo_chunks: int = 1,
) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()

    for node_id in route.node_ids:
        node = network.nodes.get(
            node_id
        )

        if node is None:
            continue

        cx, cy = world_to_chunk(
            node.x,
            node.y,
        )

        for dx in range(
            -halo_chunks,
            halo_chunks + 1,
        ):
            for dy in range(
                -halo_chunks,
                halo_chunks + 1,
            ):
                keys.add(
                    (
                        cx + dx,
                        cy + dy,
                    )
                )

    return keys


def build_fast_scenario(
    *,
    route_index: int,
    route,
    network,
    chunk_manager,
    vehicle_config_path: str | Path,
    elevation_cache_path: str | Path,
    output_dir: str | Path,
    fixed_dt_s: float = 0.05,
    background_vehicle_count: int = 40,
    traffic_speed_factor: float = 1.0,
    random_seed: int = 7,
    halo_chunks: int = 1,
) -> FastRouteScenario:
    keys = route_chunk_keys(
        network,
        route,
        halo_chunks=halo_chunks,
    )

    chunk_files = []

    for key in sorted(keys):
        path = chunk_manager.chunk_path(
            *key
        )

        if path.is_file():
            chunk_files.append(
                str(
                    path.resolve()
                )
            )

    if not chunk_files:
        raise RuntimeError(
            "No cached OSM chunk files were found for the route."
        )

    return FastRouteScenario(
        route_index=int(route_index),
        route_node_ids=tuple(
            int(node_id)
            for node_id in route.node_ids
        ),
        osm_chunk_files=tuple(
            chunk_files
        ),
        vehicle_config_path=str(
            Path(
                vehicle_config_path
            ).resolve()
        ),
        elevation_cache_path=str(
            Path(
                elevation_cache_path
            ).resolve()
        ),
        output_dir=str(
            Path(
                output_dir
            ).resolve()
        ),
        fixed_dt_s=float(
            fixed_dt_s
        ),
        background_vehicle_count=int(
            background_vehicle_count
        ),
        traffic_speed_factor=float(
            traffic_speed_factor
        ),
        random_seed=int(
            random_seed
        ),
    )


def build_candidate_scenarios(
    *,
    candidates,
    network,
    chunk_manager,
    vehicle_config_path: str | Path,
    elevation_cache_path: str | Path,
    output_dir: str | Path,
    fixed_dt_s: float = 0.05,
    background_vehicle_count: int = 40,
    traffic_speed_factor: float = 1.0,
    random_seed: int = 7,
    halo_chunks: int = 1,
) -> list[FastRouteScenario]:
    scenarios = []

    for candidate in candidates:
        scenarios.append(
            build_fast_scenario(
                route_index=candidate.candidate_index,
                route=candidate.route,
                network=network,
                chunk_manager=chunk_manager,
                vehicle_config_path=vehicle_config_path,
                elevation_cache_path=elevation_cache_path,
                output_dir=output_dir,
                fixed_dt_s=fixed_dt_s,
                background_vehicle_count=background_vehicle_count,
                traffic_speed_factor=traffic_speed_factor,
                random_seed=random_seed,
                halo_chunks=halo_chunks,
            )
        )

    return scenarios
