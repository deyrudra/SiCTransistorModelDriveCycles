from __future__ import annotations

"""
Stuttgart route-experiment generator for the F8 Experiment Lab.

This module turns an experiment definition into actual headless fixed-step
drive-cycle recordings.  It deliberately keeps route generation separate from
the electro-thermal/reliability analysis layer.

Important controls:
- route-shape experiments use the same traffic count and random seed
- traffic experiments reuse exactly the same planned route node sequence
- flat/uphill, fast/slow and stop-start pairs are selected only after all
  generated candidates have been driven, using measured drive-cycle metrics
"""

from dataclasses import dataclass
import importlib
import importlib.util
import math
import re
import shutil
from pathlib import Path
import sys
from typing import Callable

from chunks.grid import (
    CHUNK_SIZE,
    world_to_chunk,
)
from chunks.projection import (
    latlon_to_local,
)

from drive_cycles.address_search import (
    default_stuttgart_searcher,
)
from drive_cycles.address_route_service import (
    nearest_drivable_node_latlon,
)
from drive_cycles.candidate_routes import (
    plan_candidate_routes,
)
from drive_cycles.experiment_lab_analysis import (
    read_cycle_shape_metrics,
)
from drive_cycles.fast_route_simulation import (
    FastRouteScenario,
    load_network_from_chunks,
)
from drive_cycles.parallel_route_simulation import (
    run_parallel_scenarios,
)


DEFAULT_ELEVATION_CACHE_RELATIVE = (
    Path("cache")
    / "elevation"
    / "stuttgart_srtm90m.json"
)


@dataclass(frozen=True)
class GeneratedExperiment:
    experiment_key: str
    start_address: str
    end_address: str
    group_a_paths: tuple[str, ...]
    group_b_paths: tuple[str, ...]
    all_paths: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class PlannedAddressRoutes:
    start_address: str
    end_address: str
    start_node_id: int
    end_node_id: int
    candidates: tuple
    network: object
    chunk_path: Callable[[int, int], Path]


PRESETS = {
    "flat_uphill": {
        "start": "Neckarstrasse 172, 70190 Stuttgart",
        "end": "Pfaffenwaldring 47, 70569 Stuttgart",
        "candidate_count": 8,
        "traffic_count": 0,
        "seed": 7,
        "distance_tolerance_percent": 10.0,
    },
    "uphill_downhill": {
        "start": "Neckarstrasse 172, 70190 Stuttgart",
        "end": "Auf dem Haigst 37, 70597 Stuttgart",
        "candidate_count": 1,
        "traffic_count": 0,
        "seed": 7,
        "distance_tolerance_percent": 15.0,
    },
    "short_distance": {
        "start": "Neckarstrasse 172, 70190 Stuttgart",
        "end": "Mercedesstrasse 50, 70372 Stuttgart",
        "candidate_count": 5,
        "traffic_count": 20,
        "seed": 7,
        "distance_tolerance_percent": 15.0,
    },
    "long_distance": {
        "start": "Mercedesstrasse 50, 70372 Stuttgart",
        "end": "Pfaffenwaldring 47, 70569 Stuttgart",
        "candidate_count": 6,
        "traffic_count": 20,
        "seed": 7,
        "distance_tolerance_percent": 15.0,
    },
    "fast_slow": {
        "start": "Mercedesstrasse 50, 70372 Stuttgart",
        "end": "Pfaffenwaldring 47, 70569 Stuttgart",
        "candidate_count": 8,
        "traffic_count": 20,
        "seed": 7,
        "distance_tolerance_percent": 15.0,
    },
    "traffic": {
        "start": "Mercedesstrasse 50, 70372 Stuttgart",
        "end": "Pfaffenwaldring 47, 70569 Stuttgart",
        "candidate_count": 1,
        "low_traffic_count": 0,
        "high_traffic_count": 100,
        "seeds": (1, 2, 3, 4, 5),
        "distance_tolerance_percent": 0.0,
    },
    "stop_start": {
        "start": "Mercedesstrasse 50, 70372 Stuttgart",
        "end": "Pfaffenwaldring 47, 70569 Stuttgart",
        "candidate_count": 8,
        "traffic_count": 40,
        "seed": 7,
        "distance_tolerance_percent": 15.0,
    },
    "ranking": {
        "start": "Mercedesstrasse 50, 70372 Stuttgart",
        "end": "Pfaffenwaldring 47, 70569 Stuttgart",
        "candidate_count": 5,
        "traffic_count": 40,
        "seed": 7,
        "distance_tolerance_percent": 100.0,
    },
}


def preset_for(
    experiment_key: str,
) -> dict:
    if experiment_key not in PRESETS:
        raise KeyError(
            f"Unknown experiment preset: {experiment_key}"
        )

    return dict(
        PRESETS[experiment_key]
    )


def _discover_downloader(
    project_root: str | Path,
):
    root = Path(
        project_root
    ).resolve()

    for module_name in (
        "tools.osm.download_chunk",
        "simtools.osm.download_chunk",
        "osm.download_chunk",
        "download_chunk",
    ):
        try:
            module = importlib.import_module(
                module_name
            )
        except Exception:
            continue

        get_chunk = getattr(
            module,
            "get_chunk",
            None,
        )
        chunk_path = getattr(
            module,
            "chunk_path",
            None,
        )

        if (
            callable(get_chunk)
            and callable(chunk_path)
        ):
            return (
                get_chunk,
                lambda cx, cy: Path(
                    chunk_path(
                        cx,
                        cy,
                    )
                ).resolve(),
            )

    candidates = (
        # Current project layout:
        #   sim/
        #     tools/
        #       osm/
        #         download_chunk.py
        root
        / "tools"
        / "osm"
        / "download_chunk.py",

        # Also tolerate the tools folder if project_root was resolved one
        # directory above sim for a future launcher/layout change.
        root
        / "sim"
        / "tools"
        / "osm"
        / "download_chunk.py",

        # Older layouts retained for backward compatibility.
        root
        / "src"
        / "simtools"
        / "osm"
        / "download_chunk.py",
        root
        / "simtools"
        / "osm"
        / "download_chunk.py",
        root
        / "osm"
        / "download_chunk.py",
        root
        / "download_chunk.py",
    )

    for path in candidates:
        if not path.is_file():
            continue

        module_name = (
            "_f8_experiment_download_chunk_"
            + str(
                abs(
                    hash(
                        str(
                            path.resolve()
                        )
                    )
                )
            )
        )

        spec = (
            importlib.util.spec_from_file_location(
                module_name,
                path,
            )
        )

        if (
            spec is None
            or spec.loader is None
        ):
            continue

        module = (
            importlib.util.module_from_spec(
                spec
            )
        )
        spec.loader.exec_module(
            module
        )

        get_chunk = getattr(
            module,
            "get_chunk",
            None,
        )
        chunk_path = getattr(
            module,
            "chunk_path",
            None,
        )

        if (
            callable(get_chunk)
            and callable(chunk_path)
        ):
            return (
                get_chunk,
                lambda cx, cy: Path(
                    chunk_path(
                        cx,
                        cy,
                    )
                ).resolve(),
            )

    searched = [
        str(path.resolve())
        for path in candidates
    ]

    raise RuntimeError(
        "F8 could not find a usable OSM downloader with "
        "get_chunk(cx, cy) and chunk_path(cx, cy). "
        "Expected the current project file at tools/osm/download_chunk.py. "
        "Searched: " + " | ".join(searched)
    )


def _corridor_chunk_keys(
    start_xy,
    end_xy,
    *,
    radius_chunks: int = 1,
):
    x1, y1 = start_xy
    x2, y2 = end_xy

    distance = math.hypot(
        x2 - x1,
        y2 - y1,
    )

    steps = max(
        1,
        int(
            math.ceil(
                distance
                / (
                    CHUNK_SIZE
                    * 0.75
                )
            )
        ),
    )

    keys = set()

    for index in range(
        steps + 1
    ):
        alpha = (
            index
            / steps
        )

        x = (
            x1
            + (
                x2 - x1
            )
            * alpha
        )
        y = (
            y1
            + (
                y2 - y1
            )
            * alpha
        )

        cx, cy = world_to_chunk(
            x,
            y,
        )

        for dx in range(
            -radius_chunks,
            radius_chunks + 1,
        ):
            for dy in range(
                -radius_chunks,
                radius_chunks + 1,
            ):
                keys.add(
                    (
                        cx + dx,
                        cy + dy,
                    )
                )

    # More room around endpoints improves road snapping and local alternatives.
    for x, y in (
        start_xy,
        end_xy,
    ):
        cx, cy = world_to_chunk(
            x,
            y,
        )

        for dx in range(
            -2,
            3,
        ):
            for dy in range(
                -2,
                3,
            ):
                keys.add(
                    (
                        cx + dx,
                        cy + dy,
                    )
                )

    return keys


def _view_envelope_chunk_keys(
    start_xy,
    end_xy,
    *,
    margin_chunks: int,
):
    """
    Approximate the map area the visualizer would load after fitting the camera
    to both endpoints.  Unlike the thin centre-line corridor, this fills the
    complete chunk rectangle around A and B with a configurable margin.
    """
    start_cx, start_cy = world_to_chunk(
        *start_xy
    )
    end_cx, end_cy = world_to_chunk(
        *end_xy
    )

    min_cx = min(
        start_cx,
        end_cx,
    ) - int(
        margin_chunks
    )
    max_cx = max(
        start_cx,
        end_cx,
    ) + int(
        margin_chunks
    )

    min_cy = min(
        start_cy,
        end_cy,
    ) - int(
        margin_chunks
    )
    max_cy = max(
        start_cy,
        end_cy,
    ) + int(
        margin_chunks
    )

    return {
        (
            cx,
            cy,
        )
        for cx in range(
            min_cx,
            max_cx + 1,
        )
        for cy in range(
            min_cy,
            max_cy + 1,
        )
    }


def _ensure_chunk_files(
    keys,
    *,
    get_chunk,
    chunk_path,
):
    files = []

    for cx, cy in sorted(
        keys
    ):
        path = chunk_path(
            cx,
            cy,
        )

        if not path.is_file():
            result = get_chunk(
                cx,
                cy,
            )

            if result is not None:
                path = Path(
                    result
                ).resolve()

        if path.is_file():
            files.append(
                str(
                    path.resolve()
                )
            )

    if not files:
        raise RuntimeError(
            "No OSM chunk files were available for the requested Stuttgart corridor."
        )

    return tuple(
        files
    )


def _route_chunk_files(
    network,
    route,
    *,
    chunk_path,
    halo_chunks: int = 1,
):
    keys = set()

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

    files = []

    for key in sorted(
        keys
    ):
        path = chunk_path(
            *key
        )

        if path.is_file():
            files.append(
                str(
                    path.resolve()
                )
            )

    if not files:
        raise RuntimeError(
            "No cached route chunks were found after route generation."
        )

    return tuple(
        files
    )


def plan_address_candidates(
    *,
    project_root: str | Path,
    start_address: str,
    end_address: str,
    candidate_count: int,
    cost_mode: str = "time",
    diversity_penalty: float = 0.75,
    max_overlap: float = 0.90,
) -> PlannedAddressRoutes:
    project_root = Path(
        project_root
    ).resolve()

    searcher = default_stuttgart_searcher(
        project_root
    )

    start_results = searcher.search(
        start_address,
        limit=5,
    )
    end_results = searcher.search(
        end_address,
        limit=5,
    )

    if not start_results:
        raise RuntimeError(
            f"No Stuttgart address match found for start: {start_address}"
        )

    if not end_results:
        raise RuntimeError(
            f"No Stuttgart address match found for destination: {end_address}"
        )

    start_match = start_results[0]
    end_match = end_results[0]

    start_xy = latlon_to_local(
        start_match.latitude,
        start_match.longitude,
    )
    end_xy = latlon_to_local(
        end_match.latitude,
        end_match.longitude,
    )

    (
        get_chunk,
        chunk_path,
    ) = _discover_downloader(
        project_root
    )

    # F8 used to load only a narrow straight-line strip.  The interactive
    # visualizer often succeeds because fitting/panning the camera loads a much
    # wider surrounding road network.  Reproduce that behaviour here without
    # requiring the Pygame window to physically move.
    #
    # Start compact, then progressively widen the loaded map area until the
    # directed road graph connects A to B.  Previously cached chunks are reused
    # automatically by get_chunk()/chunk_path().
    last_error = None
    best_partial = None

    for expansion in range(
        1,
        7,
    ):
        corridor_keys = _corridor_chunk_keys(
            start_xy,
            end_xy,
            radius_chunks=expansion,
        )

        # Also fill the camera-like A/B bounding rectangle.  This catches road
        # corridors that bow substantially away from the straight A-B line.
        envelope_keys = _view_envelope_chunk_keys(
            start_xy,
            end_xy,
            margin_chunks=max(
                2,
                expansion,
            ),
        )

        required_keys = (
            corridor_keys
            | envelope_keys
        )

        corridor_files = _ensure_chunk_files(
            required_keys,
            get_chunk=get_chunk,
            chunk_path=chunk_path,
        )

        network = load_network_from_chunks(
            corridor_files
        )

        try:
            (
                start_node_id,
                _,
            ) = nearest_drivable_node_latlon(
                network,
                start_match.latitude,
                start_match.longitude,
                maximum_distance_m=1500.0,
            )

            (
                end_node_id,
                _,
            ) = nearest_drivable_node_latlon(
                network,
                end_match.latitude,
                end_match.longitude,
                maximum_distance_m=1500.0,
            )

            candidates = plan_candidate_routes(
                network,
                start_node_id,
                end_node_id,
                count=max(
                    1,
                    int(
                        candidate_count
                    ),
                ),
                cost_mode=cost_mode,
                diversity_penalty=diversity_penalty,
                max_overlap=max_overlap,
                max_attempts=max(
                    36,
                    int(
                        candidate_count
                    )
                    * 12,
                ),
            )
        except Exception as exc:
            last_error = exc
            candidates = []

        if candidates:
            best_partial = (
                network,
                start_node_id,
                end_node_id,
                candidates,
            )

            # A single valid route is enough to prove connectivity.  Keep
            # widening when the caller asked for alternatives and we have not
            # produced them yet; this gives the diversity search more road area.
            if (
                len(candidates)
                >= max(
                    1,
                    int(
                        candidate_count
                    ),
                )
                or expansion >= 6
            ):
                break

    if best_partial is None:
        detail = (
            f" Last routing error: {type(last_error).__name__}: {last_error}"
            if last_error is not None
            else ""
        )

        raise RuntimeError(
            "F8 loaded and progressively expanded the Stuttgart map area "
            "around both addresses, but the directed road graph still did not "
            "connect the snapped nodes."
            + detail
        )

    (
        network,
        start_node_id,
        end_node_id,
        candidates,
    ) = best_partial

    return PlannedAddressRoutes(
        start_address=(
            start_match.display_name
        ),
        end_address=(
            end_match.display_name
        ),
        start_node_id=int(
            start_node_id
        ),
        end_node_id=int(
            end_node_id
        ),
        candidates=tuple(
            candidates
        ),
        network=network,
        chunk_path=chunk_path,
    )


def _worker_elevation_cache_path(
    *,
    project_root: Path,
    unique_index: int,
) -> Path:
    """
    Give every parallel F8 worker its own elevation JSON cache.

    Sharing one cache between ProcessPool workers can cause WinError 32 on
    Windows when two workers try to replace the same temporary cache file.
    """
    canonical = (
        project_root
        / DEFAULT_ELEVATION_CACHE_RELATIVE
    ).resolve()

    # Keep the private JSON caches beside the canonical cache rather than in
    # a subdirectory. ElevationManager derives its default DEM directory as
    # cache_path.parent / "dem", so this preserves access to:
    #
    #     cache/elevation/dem/*.hgt
    #
    # while still giving every F8 process a unique JSON/.tmp filename.
    worker_dir = canonical.parent

    worker_cache = (
        worker_dir
        / (
            f"{canonical.stem}"
            f"_f8_worker_{int(unique_index):06d}"
            f"{canonical.suffix}"
        )
    ).resolve()

    if (
        not worker_cache.exists()
        and canonical.is_file()
    ):
        shutil.copy2(
            canonical,
            worker_cache,
        )

    return worker_cache


def _scenario(
    *,
    unique_index: int,
    route,
    planned: PlannedAddressRoutes,
    project_root: Path,
    vehicle_config_path: Path,
    cycles_dir: Path,
    traffic_count: int,
    random_seed: int,
    fixed_dt_s: float = 0.05,
    route_traffic_fraction: float = 0.25,
    route_traffic_min_speed_factor: float = 0.45,
    route_traffic_max_speed_factor: float = 0.85,
):
    worker_elevation_cache = (
        _worker_elevation_cache_path(
            project_root=project_root,
            unique_index=unique_index,
        )
    )

    return FastRouteScenario(
        route_index=int(
            unique_index
        ),
        route_node_ids=tuple(
            int(
                node_id
            )
            for node_id in route.node_ids
        ),
        osm_chunk_files=_route_chunk_files(
            planned.network,
            route,
            chunk_path=planned.chunk_path,
            halo_chunks=1,
        ),
        vehicle_config_path=str(
            vehicle_config_path.resolve()
        ),
        elevation_cache_path=str(
            worker_elevation_cache
        ),
        output_dir=str(
            cycles_dir.resolve()
        ),
        fixed_dt_s=float(
            fixed_dt_s
        ),
        background_vehicle_count=int(
            traffic_count
        ),
        traffic_speed_factor=1.0,
        random_seed=int(
            random_seed
        ),
        route_traffic_fraction=float(
            route_traffic_fraction
        ),
        route_traffic_min_speed_factor=float(
            route_traffic_min_speed_factor
        ),
        route_traffic_max_speed_factor=float(
            route_traffic_max_speed_factor
        ),
    )


def _run_scenarios(
    scenarios,
):
    results = run_parallel_scenarios(
        scenarios
    )

    failures = [
        result
        for result in results
        if (
            not result.success
            or not result.drive_cycle_path
        )
    ]

    if failures:
        text = "; ".join(
            (
                f"run {result.route_index}: "
                f"{result.error or 'no drive cycle'}"
            )
            for result in failures
        )

        raise RuntimeError(
            "One or more generated experiment runs failed: "
            + text
        )

    return tuple(
        str(
            Path(
                result.drive_cycle_path
            ).resolve()
        )
        for result in results
    )


def _safe_filename_token(
    value: str,
) -> str:
    token = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        str(
            value
        ).strip(),
    ).strip(
        "_"
    )

    return (
        token.lower()
        or "route"
    )


def _original_run_suffix(
    path: str | Path,
) -> str:
    """
    Keep the recorder timestamp/vehicle suffix so repeated experiment runs
    never overwrite one another.
    """
    stem = Path(
        path
    ).stem

    match = re.match(
        r"drive_cycle_(.+)$",
        stem,
    )

    if match:
        return match.group(
            1
        )

    return stem


def _rename_cycle(
    path: str | Path,
    *,
    experiment_name: str,
    route_type: str,
) -> str:
    source = Path(
        path
    ).resolve()

    if not source.is_file():
        raise FileNotFoundError(
            f"Generated drive cycle is missing: {source}"
        )

    experiment = _safe_filename_token(
        experiment_name
    )
    route = _safe_filename_token(
        route_type
    )
    suffix = _safe_filename_token(
        _original_run_suffix(
            source
        )
    )

    target = source.with_name(
        f"{experiment}__{route}__{suffix}.csv"
    )

    counter = 2

    while (
        target.exists()
        and target.resolve()
        != source
    ):
        target = source.with_name(
            f"{experiment}__{route}__{suffix}__{counter}.csv"
        )
        counter += 1

    if target.resolve() != source:
        source.rename(
            target
        )

    return str(
        target.resolve()
    )


def _rename_candidate_set(
    paths,
    *,
    experiment_name: str,
    selected_roles: dict[str, str] | None = None,
):
    """
    Rename every generated candidate.  selected_roles maps an original path
    to a semantic label such as 'flat', 'uphill', 'fast', or 'stop_start'.
    Remaining candidates retain readable candidate_N labels.
    """
    selected_roles = (
        selected_roles
        or {}
    )

    normalized_roles = {
        str(
            Path(path).resolve()
        ): role
        for path, role in selected_roles.items()
    }

    renamed = []
    mapping = {}

    candidate_number = 0

    for path in paths:
        resolved = str(
            Path(path).resolve()
        )

        role = normalized_roles.get(
            resolved
        )

        if role is None:
            candidate_number += 1
            role = (
                f"candidate_{candidate_number:02d}"
            )

        new_path = _rename_cycle(
            resolved,
            experiment_name=experiment_name,
            route_type=role,
        )

        mapping[
            resolved
        ] = new_path
        renamed.append(
            new_path
        )

    return (
        tuple(
            renamed
        ),
        mapping,
    )


def _distance_difference_percent(
    first,
    second,
):
    denominator = max(
        1e-9,
        0.5
        * (
            first["distance_km_raw"]
            + second["distance_km_raw"]
        ),
    )

    return (
        100.0
        * abs(
            first["distance_km_raw"]
            - second["distance_km_raw"]
        )
        / denominator
    )


def _choose_pair(
    paths,
    *,
    metric_key,
    tolerance_percent: float,
):
    metrics = [
        (
            path,
            read_cycle_shape_metrics(
                path
            ),
        )
        for path in paths
    ]

    if len(
        metrics
    ) < 2:
        raise RuntimeError(
            "At least two generated routes are required to choose an experiment pair."
        )

    acceptable = []

    all_pairs = []

    for left_index in range(
        len(
            metrics
        )
    ):
        for right_index in range(
            left_index + 1,
            len(
                metrics
            ),
        ):
            left_path, left = (
                metrics[
                    left_index
                ]
            )
            right_path, right = (
                metrics[
                    right_index
                ]
            )

            distance_difference = (
                _distance_difference_percent(
                    left,
                    right,
                )
            )

            metric_difference = abs(
                float(
                    left[
                        metric_key
                    ]
                )
                - float(
                    right[
                        metric_key
                    ]
                )
            )

            row = (
                metric_difference,
                -distance_difference,
                left_path,
                left,
                right_path,
                right,
            )

            all_pairs.append(
                row
            )

            if (
                distance_difference
                <= tolerance_percent
            ):
                acceptable.append(
                    row
                )

    pool = (
        acceptable
        if acceptable
        else all_pairs
    )

    if not pool:
        raise RuntimeError(
            "Could not form a pair from the generated candidate routes."
        )

    chosen = max(
        pool,
        key=lambda item: (
            item[0],
            item[1],
        ),
    )

    (
        _,
        _,
        left_path,
        left,
        right_path,
        right,
    ) = chosen

    if (
        float(
            left[
                metric_key
            ]
        )
        <= float(
            right[
                metric_key
            ]
        )
    ):
        low = (
            left_path,
            left,
        )
        high = (
            right_path,
            right,
        )
    else:
        low = (
            right_path,
            right,
        )
        high = (
            left_path,
            left,
        )

    return (
        low,
        high,
        bool(
            acceptable
        ),
    )


def _generate_candidates(
    *,
    project_root,
    vehicle_config_path,
    cycles_dir,
    start_address,
    end_address,
    candidate_count,
    traffic_count,
    random_seed,
):
    planned = plan_address_candidates(
        project_root=project_root,
        start_address=start_address,
        end_address=end_address,
        candidate_count=candidate_count,
    )

    scenarios = []

    for candidate in planned.candidates:
        scenarios.append(
            _scenario(
                unique_index=(
                    100
                    + candidate.candidate_index
                ),
                route=(
                    candidate.route
                ),
                planned=planned,
                project_root=Path(
                    project_root
                ),
                vehicle_config_path=Path(
                    vehicle_config_path
                ),
                cycles_dir=Path(
                    cycles_dir
                ),
                traffic_count=traffic_count,
                random_seed=random_seed,
            )
        )

    return (
        planned,
        _run_scenarios(
            scenarios
        ),
    )


def generate_pair_experiment(
    *,
    experiment_key: str,
    project_root: str | Path,
    vehicle_config_path: str | Path,
    cycles_dir: str | Path,
    start_address: str,
    end_address: str,
    candidate_count: int,
    distance_tolerance_percent: float,
    traffic_count: int = 20,
    random_seed: int = 7,
    low_traffic_count: int = 0,
    high_traffic_count: int = 100,
    traffic_seeds=(1, 2, 3, 4, 5),
) -> GeneratedExperiment:
    project_root = Path(
        project_root
    ).resolve()
    vehicle_config_path = Path(
        vehicle_config_path
    ).resolve()
    cycles_dir = Path(
        cycles_dir
    ).resolve()

    cycles_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if experiment_key == "uphill_downhill":
        forward = plan_address_candidates(
            project_root=project_root,
            start_address=start_address,
            end_address=end_address,
            candidate_count=1,
        )

        reverse = plan_address_candidates(
            project_root=project_root,
            start_address=end_address,
            end_address=start_address,
            candidate_count=1,
        )

        forward_path = _run_scenarios(
            [
                _scenario(
                    unique_index=201,
                    route=(
                        forward.candidates[
                            0
                        ].route
                    ),
                    planned=forward,
                    project_root=project_root,
                    vehicle_config_path=vehicle_config_path,
                    cycles_dir=cycles_dir,
                    traffic_count=traffic_count,
                    random_seed=random_seed,
                )
            ]
        )

        reverse_path = _run_scenarios(
            [
                _scenario(
                    unique_index=202,
                    route=(
                        reverse.candidates[
                            0
                        ].route
                    ),
                    planned=reverse,
                    project_root=project_root,
                    vehicle_config_path=vehicle_config_path,
                    cycles_dir=cycles_dir,
                    traffic_count=traffic_count,
                    random_seed=random_seed,
                )
            ]
        )

        renamed_forward = (
            _rename_cycle(
                forward_path[0],
                experiment_name="exp02_uphill_vs_downhill",
                route_type="uphill_A_to_B",
            ),
        )

        renamed_reverse = (
            _rename_cycle(
                reverse_path[0],
                experiment_name="exp02_uphill_vs_downhill",
                route_type="downhill_B_to_A",
            ),
        )

        return GeneratedExperiment(
            experiment_key=(
                experiment_key
            ),
            start_address=(
                start_address
            ),
            end_address=(
                end_address
            ),
            group_a_paths=(
                renamed_forward
            ),
            group_b_paths=(
                renamed_reverse
            ),
            all_paths=(
                renamed_forward
                + renamed_reverse
            ),
            notes=(
                "Group A is A to B; Group B is the reverse B to A corridor.",
            ),
        )

    if experiment_key == "traffic":
        planned = plan_address_candidates(
            project_root=project_root,
            start_address=start_address,
            end_address=end_address,
            candidate_count=1,
        )

        route = (
            planned.candidates[
                0
            ].route
        )

        scenarios = []
        low_indices = []
        high_indices = []
        unique = 300

        for seed in traffic_seeds:
            unique += 1
            low_indices.append(
                unique
            )
            scenarios.append(
                _scenario(
                    unique_index=unique,
                    route=route,
                    planned=planned,
                    project_root=project_root,
                    vehicle_config_path=vehicle_config_path,
                    cycles_dir=cycles_dir,
                    traffic_count=low_traffic_count,
                    random_seed=int(
                        seed
                    ),
                    route_traffic_fraction=1.0,
                    route_traffic_min_speed_factor=0.45,
                    route_traffic_max_speed_factor=0.85,
                )
            )

        for seed in traffic_seeds:
            unique += 1
            high_indices.append(
                unique
            )
            scenarios.append(
                _scenario(
                    unique_index=unique,
                    route=route,
                    planned=planned,
                    project_root=project_root,
                    vehicle_config_path=vehicle_config_path,
                    cycles_dir=cycles_dir,
                    traffic_count=high_traffic_count,
                    random_seed=int(
                        seed
                    ),
                    route_traffic_fraction=1.0,
                    route_traffic_min_speed_factor=0.45,
                    route_traffic_max_speed_factor=0.85,
                )
            )

        results = run_parallel_scenarios(
            scenarios
        )

        by_index = {
            result.route_index: result
            for result in results
        }

        def paths_for(
            indices
        ):
            output = []

            for index in indices:
                result = by_index.get(
                    index
                )

                if (
                    result is None
                    or not result.success
                    or not result.drive_cycle_path
                ):
                    raise RuntimeError(
                        f"Traffic experiment run {index} failed: "
                        f"{None if result is None else result.error}"
                    )

                output.append(
                    str(
                        Path(
                            result.drive_cycle_path
                        ).resolve()
                    )
                )

            return tuple(
                output
            )

        low_paths = paths_for(
            low_indices
        )
        high_paths = paths_for(
            high_indices
        )

        renamed_low = tuple(
            _rename_cycle(
                path,
                experiment_name="exp06_traffic",
                route_type=(
                    f"low_traffic_{low_traffic_count}_vehicles_seed_{int(seed):02d}"
                ),
            )
            for path, seed in zip(
                low_paths,
                traffic_seeds,
            )
        )

        renamed_high = tuple(
            _rename_cycle(
                path,
                experiment_name="exp06_traffic",
                route_type=(
                    f"heavy_traffic_{high_traffic_count}_vehicles_seed_{int(seed):02d}"
                ),
            )
            for path, seed in zip(
                high_paths,
                traffic_seeds,
            )
        )

        return GeneratedExperiment(
            experiment_key=(
                experiment_key
            ),
            start_address=(
                start_address
            ),
            end_address=(
                end_address
            ),
            group_a_paths=(
                renamed_low
            ),
            group_b_paths=(
                renamed_high
            ),
            all_paths=(
                renamed_low
                + renamed_high
            ),
            notes=(
                (
                    "Exact same planned route geometry; "
                    f"Group A={low_traffic_count} background vehicles, "
                    f"Group B={high_traffic_count}."
                ),
                (
                    "Seeds: "
                    + ", ".join(
                        str(
                            seed
                        )
                        for seed
                        in traffic_seeds
                    )
                ),
            ),
        )

    (
        _planned,
        paths,
    ) = _generate_candidates(
        project_root=project_root,
        vehicle_config_path=vehicle_config_path,
        cycles_dir=cycles_dir,
        start_address=start_address,
        end_address=end_address,
        candidate_count=candidate_count,
        traffic_count=traffic_count,
        random_seed=random_seed,
    )

    metric_key = {
        "flat_uphill": "ascent_m",
        "short_distance": "duration_s_raw",
        "long_distance": "duration_s_raw",
        "fast_slow": "average_speed_kmh",
        "stop_start": "stopped_time_percent",
    }.get(
        experiment_key
    )

    if metric_key is None:
        raise ValueError(
            f"Unsupported pair experiment: {experiment_key}"
        )

    (
        low,
        high,
        within_tolerance,
    ) = _choose_pair(
        paths,
        metric_key=metric_key,
        tolerance_percent=float(
            distance_tolerance_percent
        ),
    )

    low_path, low_metrics = (
        low
    )
    high_path, high_metrics = (
        high
    )

    if experiment_key == "flat_uphill":
        note = (
            "Auto-selected the closest-distance candidate pair with the "
            "largest available total-ascent difference."
        )
    elif experiment_key in (
        "short_distance",
        "long_distance",
    ):
        note = (
            "Auto-selected two similar-distance alternatives with the "
            "largest travel-time difference."
        )
    elif experiment_key == "fast_slow":
        note = (
            "Auto-selected two similar-distance alternatives with the "
            "largest average-speed difference."
        )
    else:
        note = (
            "Auto-selected two similar-distance alternatives with the "
            "largest stopped-time difference."
        )

    distance_difference = (
        _distance_difference_percent(
            low_metrics,
            high_metrics,
        )
    )

    notes = [
        note,
        (
            f"Selected-route distance difference: "
            f"{distance_difference:.2f}% "
            f"(requested tolerance {distance_tolerance_percent:.1f}%)."
        ),
    ]

    if not within_tolerance:
        notes.append(
            "WARNING: no candidate pair met the requested distance tolerance; "
            "F8 used the best available pair."
        )

    if experiment_key == "fast_slow":
        # GUI convention: Group A = faster route, Group B = slower route.
        group_a_path = high_path
        group_b_path = low_path
    else:
        group_a_path = low_path
        group_b_path = high_path

    experiment_filename_name = {
        "flat_uphill": "exp01_flat_vs_uphill",
        "short_distance": "exp03_short_distance",
        "long_distance": "exp04_long_distance",
        "fast_slow": "exp05_fast_vs_slow_roads",
        "stop_start": "exp07_stop_start_vs_free_flow",
    }[
        experiment_key
    ]

    semantic_roles = {
        "flat_uphill": (
            "flat_lower_ascent",
            "uphill_higher_ascent",
        ),
        "short_distance": (
            "short_route_A",
            "short_route_B",
        ),
        "long_distance": (
            "long_route_A",
            "long_route_B",
        ),
        "fast_slow": (
            "fast_route",
            "slow_route",
        ),
        "stop_start": (
            "free_flow",
            "stop_start",
        ),
    }[
        experiment_key
    ]

    selected_roles = {
        str(
            Path(
                group_a_path
            ).resolve()
        ): semantic_roles[0],
        str(
            Path(
                group_b_path
            ).resolve()
        ): semantic_roles[1],
    }

    (
        renamed_all_paths,
        rename_mapping,
    ) = _rename_candidate_set(
        paths,
        experiment_name=experiment_filename_name,
        selected_roles=selected_roles,
    )

    renamed_group_a = rename_mapping[
        str(
            Path(
                group_a_path
            ).resolve()
        )
    ]

    renamed_group_b = rename_mapping[
        str(
            Path(
                group_b_path
            ).resolve()
        )
    ]

    return GeneratedExperiment(
        experiment_key=experiment_key,
        start_address=start_address,
        end_address=end_address,
        group_a_paths=(
            renamed_group_a,
        ),
        group_b_paths=(
            renamed_group_b,
        ),
        all_paths=(
            renamed_all_paths
        ),
        notes=tuple(
            notes
        ),
    )


def generate_ranking_experiment(
    *,
    project_root: str | Path,
    vehicle_config_path: str | Path,
    cycles_dir: str | Path,
    start_address: str,
    end_address: str,
    candidate_count: int = 5,
    traffic_count: int = 40,
    random_seed: int = 7,
) -> GeneratedExperiment:
    (
        _planned,
        paths,
    ) = _generate_candidates(
        project_root=project_root,
        vehicle_config_path=vehicle_config_path,
        cycles_dir=cycles_dir,
        start_address=start_address,
        end_address=end_address,
        candidate_count=candidate_count,
        traffic_count=traffic_count,
        random_seed=random_seed,
    )

    renamed_paths = tuple(
        _rename_cycle(
            path,
            experiment_name="exp08_route_ranking",
            route_type=f"candidate_{index:02d}",
        )
        for index, path in enumerate(
            paths,
            start=1,
        )
    )

    return GeneratedExperiment(
        experiment_key="ranking",
        start_address=start_address,
        end_address=end_address,
        group_a_paths=tuple(),
        group_b_paths=tuple(),
        all_paths=(
            renamed_paths
        ),
        notes=(
            (
                f"Generated {len(renamed_paths)} diverse candidate routes for the same "
                "A/B endpoints using identical traffic settings."
            ),
        ),
    )
