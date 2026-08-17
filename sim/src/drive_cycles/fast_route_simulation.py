from __future__ import annotations

"""
Fast, deterministic, fixed-step route simulation.

Purpose
-------
Run an EgoVehicle route as fast as the CPU can execute it without increasing
the numerical timestep. Rendering and wall-clock timing are completely removed.

The default physics timestep is 0.05 s, matching the project's drive-cycle
recording grid. "Faster" therefore means more simulation steps per real second,
not larger simulation steps.

This module is also designed to be called from worker PROCESSES so several
candidate routes can be simulated independently in parallel.
"""

from dataclasses import dataclass, asdict
from collections import defaultdict
import copy
import csv
import json
import math
import random
import time
from pathlib import Path
from typing import Any, Iterable

import xml.etree.ElementTree as ET

from traffic.road_network import RoadNetwork, RoadSegment
from traffic.traffic_light import TrafficLight
from traffic.intersection import Intersection
from traffic.simulation import Simulation
from traffic.vehicle import Vehicle

from drive_cycles.route_planner import Route
from drive_cycles.ego_vehicle import EgoVehicle
from drive_cycles.drive_cycle_recorder import DriveCycleRecorder
from drive_cycles.vehicle_config import load_vehicle_config
from drive_cycles.elevation_data import ElevationManager


DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service",
}


@dataclass(frozen=True)
class FastRouteScenario:
    route_index: int
    route_node_ids: tuple[int, ...]
    osm_chunk_files: tuple[str, ...]
    vehicle_config_path: str
    elevation_cache_path: str
    output_dir: str

    fixed_dt_s: float = 0.05
    max_simulation_time_s: float = 7200.0
    background_vehicle_count: int = 40
    traffic_speed_factor: float = 1.0
    random_seed: int = 7


@dataclass(frozen=True)
class FastRouteResult:
    route_index: int
    success: bool
    error: str | None

    drive_cycle_path: str | None

    simulated_time_s: float
    wall_time_s: float
    realtime_factor: float
    physics_steps: int

    route_distance_m: float
    arrived: bool


class VehicleFrameIndex:
    """
    Same O(V log V) lookup concept used by the visualizer.
    """
    def __init__(self) -> None:
        self.ahead_distance: dict[int, float] = {}

    def rebuild(self, vehicles: list) -> None:
        groups: dict[int, list] = defaultdict(list)

        for vehicle in vehicles:
            segment = getattr(vehicle, "segment", None)
            if segment is not None:
                groups[id(segment)].append(vehicle)

        ahead: dict[int, float] = {}

        for group in groups.values():
            if len(group) < 2:
                continue

            group.sort(
                key=lambda vehicle: vehicle.position
            )

            for current, nxt in zip(
                group,
                group[1:],
            ):
                ahead[id(current)] = (
                    nxt.position
                    - current.position
                )

        self.ahead_distance = ahead

    def distance_ahead(
        self,
        vehicle,
    ):
        return self.ahead_distance.get(
            id(vehicle)
        )


def _fast_distance_to_vehicle_ahead(vehicle):
    index = getattr(
        vehicle.simulation,
        "_vehicle_frame_index",
        None,
    )

    if index is None:
        return None

    return index.distance_ahead(
        vehicle
    )


def _fast_traffic_light_state(vehicle):
    cache = getattr(
        vehicle.simulation,
        "_signal_state_cache",
        None,
    )

    if cache is None:
        return None

    return cache.get(
        vehicle.segment.v
    )


def install_fast_vehicle_lookups() -> None:
    Vehicle.distance_to_vehicle_ahead = (
        _fast_distance_to_vehicle_ahead
    )
    Vehicle.get_traffic_light_state = (
        _fast_traffic_light_state
    )


def _append_road_segments(
    network: RoadNetwork,
    road_ids: Iterable[int],
) -> None:
    for road_id in road_ids:
        road = network.roads.get(
            road_id
        )

        if road is None:
            continue

        for index in range(
            len(road.nodes) - 1
        ):
            u = road.nodes[index]
            v = road.nodes[index + 1]

            if (
                u not in network.nodes
                or v not in network.nodes
            ):
                continue

            pairs = (
                [(u, v)]
                if road.oneway
                else [(u, v), (v, u)]
            )

            for a, b in pairs:
                segment = RoadSegment(
                    a,
                    b,
                    road,
                )

                na = network.nodes[a]
                nb = network.nodes[b]

                segment.length = math.hypot(
                    nb.x - na.x,
                    nb.y - na.y,
                )

                network.segments.append(
                    segment
                )

                network.outgoing.setdefault(
                    a,
                    [],
                ).append(
                    segment
                )


def _rebuild_intersections(
    network: RoadNetwork,
) -> None:
    network.traffic_lights = {
        node.id: network.traffic_lights.get(
            node.id,
            TrafficLight(node.id),
        )
        for node in network.nodes.values()
        if node.traffic_light
    }

    incoming = defaultdict(list)

    for segment in network.segments:
        incoming[segment.v].append(
            segment
        )

    signals = [
        node
        for node in network.nodes.values()
        if node.traffic_light
    ]

    cell = 40.0
    buckets = defaultdict(list)

    for node in signals:
        buckets[
            (
                math.floor(node.x / cell),
                math.floor(node.y / cell),
            )
        ].append(node)

    used = set()
    intersections = []

    for node in signals:
        if node.id in used:
            continue

        intersection = Intersection(
            len(intersections)
        )

        bx = math.floor(
            node.x / cell
        )
        by = math.floor(
            node.y / cell
        )

        nearby = []

        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nearby.extend(
                    buckets.get(
                        (bx + dx, by + dy),
                        (),
                    )
                )

        for other in nearby:
            if other.id in used:
                continue

            if math.hypot(
                other.x - node.x,
                other.y - node.y,
            ) <= 40.0:
                intersection.signal_nodes.append(
                    other.id
                )
                used.add(
                    other.id
                )

        angles = []

        for node_id in intersection.signal_nodes:
            inc = incoming.get(
                node_id
            )

            if not inc:
                continue

            segment = inc[0]
            a = network.nodes[
                segment.u
            ]
            b = network.nodes[
                segment.v
            ]

            angles.append(
                (
                    node_id,
                    math.atan2(
                        b.y - a.y,
                        b.x - a.x,
                    ),
                )
            )

        if angles:
            reference = angles[0][1]

            for node_id, angle in angles:
                diff = abs(
                    reference - angle
                )

                while diff > math.pi:
                    diff -= math.pi

                destination = (
                    intersection.phase_a
                    if abs(diff) < math.radians(45)
                    else intersection.phase_b
                )

                destination.append(
                    node_id
                )

        if (
            intersection.phase_a
            or intersection.phase_b
        ):
            intersections.append(
                intersection
            )

    network.intersections = intersections


def load_network_from_chunks(
    chunk_files: Iterable[str | Path],
) -> RoadNetwork:
    network = RoadNetwork()

    network.segments = []
    network.outgoing = {}

    for file_value in chunk_files:
        path = Path(
            file_value
        )

        if not path.is_file():
            continue

        root = ET.parse(
            path
        ).getroot()

        old_road_ids = set(
            network.roads
        )

        network.load_nodes(
            root
        )
        network.load_roads(
            root
        )

        new_road_ids = (
            set(network.roads)
            - old_road_ids
        )

        _append_road_segments(
            network,
            new_road_ids,
        )

    _rebuild_intersections(
        network
    )

    return network


def reconstruct_route(
    network: RoadNetwork,
    node_ids: Iterable[int],
) -> Route:
    ids = list(
        node_ids
    )

    if len(ids) < 2:
        raise ValueError(
            "A route needs at least two node IDs."
        )

    segments = []
    distance_m = 0.0
    estimated_time_s = 0.0

    for u, v in zip(
        ids,
        ids[1:],
    ):
        choices = [
            segment
            for segment in network.outgoing.get(
                u,
                (),
            )
            if segment.v == v
        ]

        if not choices:
            raise RuntimeError(
                f"Route edge {u} -> {v} is not present in the worker network."
            )

        # Multiple OSM ways can occasionally connect the same node pair.
        # Prefer the shortest physical segment.
        segment = min(
            choices,
            key=lambda item: item.length,
        )

        segments.append(
            segment
        )

        distance_m += (
            segment.length
        )

        speed = (
            segment.speed_limit
            if segment.speed_limit
            else 10.0
        )

        estimated_time_s += (
            segment.length
            / max(
                1.0,
                speed,
            )
        )

    return Route(
        node_ids=ids,
        segments=segments,
        distance_m=distance_m,
        estimated_time_s=estimated_time_s,
    )


def _refresh_signal_cache(
    simulation: Simulation,
    network: RoadNetwork,
) -> None:
    cache = {}

    for intersection in network.intersections:
        state = intersection.state

        if state == "A_GREEN":
            a_state, b_state = (
                "green",
                "red",
            )
        elif state == "A_YELLOW":
            a_state, b_state = (
                "yellow",
                "red",
            )
        elif state == "B_GREEN":
            a_state, b_state = (
                "red",
                "green",
            )
        else:
            a_state, b_state = (
                "red",
                "yellow",
            )

        for node_id in intersection.phase_a:
            cache[node_id] = a_state

        for node_id in intersection.phase_b:
            cache[node_id] = b_state

    simulation._signal_state_cache = cache


def _spawn_background_vehicles(
    network: RoadNetwork,
    simulation: Simulation,
    count: int,
    random_seed: int,
) -> None:
    rng = random.Random(
        random_seed
    )

    candidates = [
        segment
        for segment in network.segments
        if (
            segment.length > 2.0
            and segment.u in network.nodes
            and segment.v in network.nodes
        )
    ]

    if not candidates:
        return

    next_id = 0

    for offset in range(
        max(
            0,
            int(count),
        )
    ):
        segment = rng.choice(
            candidates
        )

        vehicle = Vehicle(
            next_id + offset,
            segment,
            network,
            simulation,
        )

        vehicle.position = rng.uniform(
            0.0,
            max(
                0.0,
                segment.length - 0.1,
            ),
        )

        vehicle.speed = rng.uniform(
            0.0,
            min(
                segment.speed_limit
                or 10.0,
                8.0,
            ),
        )

        simulation.add_vehicle(
            vehicle
        )


def _grade_for_ego(
    elevation: ElevationManager,
    ego: EgoVehicle,
    network: RoadNetwork,
) -> float:
    if ego.segment is None:
        return 0.0

    grade = elevation.segment_grade_deg(
        ego.segment,
        network,
    )

    return (
        0.0
        if grade is None
        else float(grade)
    )


def run_fast_route_scenario(
    scenario: FastRouteScenario,
) -> FastRouteResult:
    """
    Process-safe top-level worker entrypoint.
    """
    wall_start = time.perf_counter()

    try:
        if scenario.fixed_dt_s <= 0.0:
            raise ValueError(
                "fixed_dt_s must be positive."
            )

        install_fast_vehicle_lookups()

        network = load_network_from_chunks(
            scenario.osm_chunk_files
        )

        route = reconstruct_route(
            network,
            scenario.route_node_ids,
        )

        simulation = Simulation(
            network
        )

        simulation.speed = 1.0
        simulation.traffic_speed_factor = (
            float(
                scenario.traffic_speed_factor
            )
        )

        frame_index = VehicleFrameIndex()
        simulation._vehicle_frame_index = frame_index
        simulation._signal_state_cache = {}

        _spawn_background_vehicles(
            network,
            simulation,
            scenario.background_vehicle_count,
            scenario.random_seed,
        )

        vehicle_config = load_vehicle_config(
            scenario.vehicle_config_path
        )

        elevation = ElevationManager(
            cache_path=Path(
                scenario.elevation_cache_path
            )
        )

        # Worker mode should not alter the numerical step just to wait for
        # elevation. Cached elevation is expected after route planning. If some
        # entries are missing, request them once and poll without sleeping the
        # simulation itself.
        elevation.request_nodes(
            network,
            route.node_ids,
        )

        wait_start = time.monotonic()

        while not elevation.route_ready(
            route.node_ids
        ):
            elevation.poll()

            if (
                time.monotonic()
                - wait_start
                > 30.0
            ):
                raise TimeoutError(
                    "Elevation was not ready within 30 seconds."
                )

            # This wait is outside simulated time and only occurs for missing
            # elevation cache entries.
            time.sleep(
                0.01
            )

        ego_id = (
            max(
                (
                    vehicle.id
                    for vehicle in simulation.vehicles
                ),
                default=-1,
            )
            + 100000
            + int(scenario.route_index) * 1000
        )

        ego = EgoVehicle(
            vehicle_id=ego_id,
            route_segments=route.segments,
            network=network,
            simulation=simulation,
            config=vehicle_config,
        )

        output_dir = Path(
            scenario.output_dir
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        recorder = DriveCycleRecorder(
            vehicle_id=ego_id,
            output_dir=output_dir,
            route_node_count=len(
                route.node_ids
            ),
            route_distance_m=route.distance_m,
            sample_interval_s=scenario.fixed_dt_s,
        )

        sim_time_s = 0.0
        grade_deg = _grade_for_ego(
            elevation,
            ego,
            network,
        )

        recorder.start(
            sim_time_s,
            initial_speed_mps=ego.speed,
            grade_deg=grade_deg,
        )

        physics_steps = 0

        while (
            not ego.arrived
            and sim_time_s
            < scenario.max_simulation_time_s
        ):
            # IMPORTANT:
            # fixed dt never changes with wall-clock speed or CPU load.
            dt = scenario.fixed_dt_s

            frame_index.rebuild(
                simulation.vehicles
            )

            _refresh_signal_cache(
                simulation,
                network,
            )

            simulation.update(
                dt
            )

            grade_deg = _grade_for_ego(
                elevation,
                ego,
                network,
            )

            ego.update(
                dt,
                grade_deg=grade_deg,
            )

            sim_time_s += dt
            physics_steps += 1

            grade_deg = _grade_for_ego(
                elevation,
                ego,
                network,
            )

            recorder.record(
                simulation_time_s=sim_time_s,
                speed_mps=ego.speed,
                grade_deg=grade_deg,
            )

        status = (
            "arrived"
            if ego.arrived
            else "max_simulation_time"
        )

        # Persist route endpoint provenance in F5/headless recordings too.
        # Interactive recordings already include this metadata; without it the
        # F7 Validation Lab cannot query the local DEM for endpoint elevations.
        endpoint_metadata = {}

        if scenario.route_node_ids:
            start_node_id = scenario.route_node_ids[0]
            end_node_id = scenario.route_node_ids[-1]

            endpoint_metadata["start_node_id"] = start_node_id
            endpoint_metadata["end_node_id"] = end_node_id

            for prefix, node_id in (
                ("start", start_node_id),
                ("end", end_node_id),
            ):
                node = network.nodes.get(node_id)

                if node is None:
                    continue

                if hasattr(node, "lat"):
                    endpoint_metadata[f"{prefix}_lat"] = float(node.lat)

                for longitude_name in ("lon", "lng", "longitude"):
                    if hasattr(node, longitude_name):
                        endpoint_metadata[f"{prefix}_lon"] = float(
                            getattr(node, longitude_name)
                        )
                        break

                endpoint_metadata[f"{prefix}_location"] = (
                    f"OSM node {node_id}"
                )

        path = recorder.save(
            status=status,
            extra_metadata={
                "execution_mode": "headless_fixed_step",
                "fixed_dt_s": scenario.fixed_dt_s,
                "route_index": scenario.route_index,
                "route_candidate_index": scenario.route_index,
                "traffic_speed_factor": scenario.traffic_speed_factor,
                "vehicle_config": vehicle_config.name,
                "random_seed": scenario.random_seed,
                **endpoint_metadata,
            },
        )

        elevation.close()

        wall_time_s = (
            time.perf_counter()
            - wall_start
        )

        realtime_factor = (
            sim_time_s
            / wall_time_s
            if wall_time_s > 0.0
            else math.inf
        )

        return FastRouteResult(
            route_index=scenario.route_index,
            success=True,
            error=None,
            drive_cycle_path=(
                str(path)
                if path is not None
                else None
            ),
            simulated_time_s=sim_time_s,
            wall_time_s=wall_time_s,
            realtime_factor=realtime_factor,
            physics_steps=physics_steps,
            route_distance_m=route.distance_m,
            arrived=ego.arrived,
        )

    except Exception as exc:
        wall_time_s = (
            time.perf_counter()
            - wall_start
        )

        return FastRouteResult(
            route_index=scenario.route_index,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            drive_cycle_path=None,
            simulated_time_s=0.0,
            wall_time_s=wall_time_s,
            realtime_factor=0.0,
            physics_steps=0,
            route_distance_m=0.0,
            arrived=False,
        )


def write_fast_result_json(
    result: FastRouteResult,
    path: str | Path,
) -> Path:
    output = Path(
        path
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            asdict(result),
            handle,
            indent=2,
        )

    return output
