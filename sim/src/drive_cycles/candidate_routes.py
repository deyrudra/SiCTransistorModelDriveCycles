from __future__ import annotations

"""
Generate multiple diverse candidate routes between the same start/end nodes.

This module leaves the existing single-route route_planner.py intact.

Strategy:
- route 1 is the normal minimum-cost path
- later routes use an increasing penalty on road segments already used by
  previously accepted routes
- near-duplicate candidates are rejected using edge-set overlap

This produces useful route alternatives for downstream MissionTwin comparison
without requiring changes to the road-network data model.
"""

from dataclasses import dataclass
import heapq
import math
from typing import Any

from drive_cycles.route_planner import Route


@dataclass(frozen=True)
class CandidateRoute:
    candidate_index: int
    route: Route
    overlap_with_best: float
    diversity_penalty: float


def _node_id(node: Any) -> Any:
    for name in ("id", "node_id", "osm_id"):
        if hasattr(node, name):
            return getattr(node, name)
    return node


def _segment_target_id(segment: Any) -> Any:
    for name in (
        "v",
        "end_node_id",
        "to_node_id",
        "target_node_id",
        "end_id",
        "to_id",
    ):
        if hasattr(segment, name):
            return getattr(segment, name)

    for name in (
        "end_node",
        "to_node",
        "target_node",
    ):
        if hasattr(segment, name):
            return _node_id(getattr(segment, name))

    raise AttributeError(
        "Could not determine a road segment's target node. "
        "Expected end_node/end_node_id (or compatible attribute)."
    )


def _segment_key(segment: Any, start_id: Any | None = None) -> tuple:
    for name in ("id", "segment_id", "way_segment_id"):
        if hasattr(segment, name):
            value = getattr(segment, name)
            try:
                hash(value)
                return ("id", value)
            except TypeError:
                pass

    target = _segment_target_id(segment)

    if start_id is not None:
        return ("edge", start_id, target)

    return ("object", id(segment))


def _segment_length_m(segment: Any) -> float:
    for name in ("length", "length_m", "distance_m"):
        if hasattr(segment, name):
            return max(0.01, float(getattr(segment, name)))

    raise AttributeError(
        "Road segment has no length/length_m/distance_m attribute."
    )


def _segment_speed_mps(segment: Any) -> float:
    for name in (
        "speed_limit",
        "speed_limit_mps",
        "max_speed_mps",
    ):
        if hasattr(segment, name):
            value = getattr(segment, name)
            if value is not None:
                try:
                    return max(1.0, float(value))
                except (TypeError, ValueError):
                    pass

    return 10.0


def _base_edge_cost(segment: Any, cost_mode: str) -> float:
    length = _segment_length_m(segment)

    if cost_mode == "distance":
        return length

    if cost_mode == "time":
        return length / _segment_speed_mps(segment)

    raise ValueError(
        "cost_mode must be 'time' or 'distance'."
    )


def _node_xy(network: Any, node_id: Any) -> tuple[float, float] | None:
    nodes = getattr(network, "nodes", None)

    if not isinstance(nodes, dict):
        return None

    node = nodes.get(node_id)

    if node is None:
        return None

    if hasattr(node, "x") and hasattr(node, "y"):
        return float(node.x), float(node.y)

    return None


def _heuristic(
    network: Any,
    node_id: Any,
    goal_id: Any,
    cost_mode: str,
) -> float:
    a = _node_xy(network, node_id)
    b = _node_xy(network, goal_id)

    if a is None or b is None:
        return 0.0

    distance = math.hypot(
        b[0] - a[0],
        b[1] - a[1],
    )

    if cost_mode == "distance":
        return distance

    # Optimistic urban-road heuristic.
    return distance / 36.0


def _reconstruct_route(
    network: Any,
    start_node_id: Any,
    end_node_id: Any,
    came_from: dict,
    cost_mode: str,
) -> Route:
    node_ids = [end_node_id]
    segments = []

    current = end_node_id

    while current != start_node_id:
        previous, segment = came_from[current]
        segments.append(segment)
        node_ids.append(previous)
        current = previous

    node_ids.reverse()
    segments.reverse()

    distance_m = sum(
        _segment_length_m(segment)
        for segment in segments
    )

    estimated_time_s = sum(
        _segment_length_m(segment)
        / _segment_speed_mps(segment)
        for segment in segments
    )

    return Route(
        node_ids=node_ids,
        segments=segments,
        distance_m=distance_m,
        estimated_time_s=estimated_time_s,
    )


def _penalized_route(
    network: Any,
    start_node_id: Any,
    end_node_id: Any,
    *,
    cost_mode: str,
    edge_usage: dict[tuple, int],
    diversity_penalty: float,
) -> Route | None:
    outgoing = getattr(network, "outgoing", None)

    if outgoing is None:
        raise AttributeError(
            "Road network must expose an outgoing adjacency mapping."
        )

    queue = [
        (
            _heuristic(
                network,
                start_node_id,
                end_node_id,
                cost_mode,
            ),
            0.0,
            start_node_id,
        )
    ]

    g_score = {
        start_node_id: 0.0
    }

    came_from = {}

    while queue:
        _, current_cost, current = heapq.heappop(
            queue
        )

        if current == end_node_id:
            return _reconstruct_route(
                network,
                start_node_id,
                end_node_id,
                came_from,
                cost_mode,
            )

        if current_cost > g_score.get(
            current,
            math.inf,
        ) + 1e-12:
            continue

        for segment in outgoing.get(
            current,
            [],
        ):
            target = _segment_target_id(
                segment
            )

            key = _segment_key(
                segment,
                current,
            )

            used_count = edge_usage.get(
                key,
                0,
            )

            base_cost = _base_edge_cost(
                segment,
                cost_mode,
            )

            penalty_multiplier = (
                1.0
                + diversity_penalty
                * used_count
            )

            step_cost = (
                base_cost
                * penalty_multiplier
            )

            new_cost = (
                current_cost
                + step_cost
            )

            if new_cost + 1e-12 >= g_score.get(
                target,
                math.inf,
            ):
                continue

            g_score[target] = new_cost
            came_from[target] = (
                current,
                segment,
            )

            priority = (
                new_cost
                + _heuristic(
                    network,
                    target,
                    end_node_id,
                    cost_mode,
                )
            )

            heapq.heappush(
                queue,
                (
                    priority,
                    new_cost,
                    target,
                ),
            )

    return None


def _route_edge_set(route: Route) -> set[tuple]:
    keys = set()

    for index, segment in enumerate(
        route.segments
    ):
        start_id = (
            route.node_ids[index]
            if index < len(route.node_ids)
            else None
        )

        keys.add(
            _segment_key(
                segment,
                start_id,
            )
        )

    return keys


def route_overlap(
    route_a: Route,
    route_b: Route,
) -> float:
    """
    Jaccard edge overlap in [0, 1].
    """
    a = _route_edge_set(
        route_a
    )
    b = _route_edge_set(
        route_b
    )

    if not a and not b:
        return 1.0

    union = a | b

    if not union:
        return 0.0

    return len(a & b) / len(union)


def plan_candidate_routes(
    network: Any,
    start_node_id: Any,
    end_node_id: Any,
    *,
    count: int = 3,
    cost_mode: str = "time",
    diversity_penalty: float = 0.75,
    max_overlap: float = 0.90,
    max_attempts: int = 24,
) -> list[CandidateRoute]:
    """
    Generate up to `count` diverse candidate routes.

    The first route is the unpenalized optimum. Reused edges become more
    expensive for subsequent searches, encouraging alternative corridors.
    """
    if count < 1:
        raise ValueError(
            "count must be >= 1."
        )

    if not 0.0 <= max_overlap <= 1.0:
        raise ValueError(
            "max_overlap must be between 0 and 1."
        )

    accepted: list[Route] = []
    edge_usage: dict[tuple, int] = {}

    attempt = 0

    while (
        len(accepted) < count
        and attempt < max_attempts
    ):
        attempt += 1

        # Increase the diversity pressure gradually if early attempts keep
        # reproducing very similar routes.
        attempt_penalty = (
            0.0
            if not accepted
            else diversity_penalty
            * (
                1.0
                + 0.20
                * max(
                    0,
                    attempt - len(accepted) - 1,
                )
            )
        )

        route = _penalized_route(
            network,
            start_node_id,
            end_node_id,
            cost_mode=cost_mode,
            edge_usage=edge_usage,
            diversity_penalty=attempt_penalty,
        )

        if route is None:
            break

        if accepted:
            overlaps = [
                route_overlap(
                    route,
                    previous,
                )
                for previous in accepted
            ]

            if max(overlaps) > max_overlap:
                # Penalize this route more strongly before trying again.
                for index, segment in enumerate(
                    route.segments
                ):
                    start_id = (
                        route.node_ids[index]
                        if index < len(route.node_ids)
                        else None
                    )

                    key = _segment_key(
                        segment,
                        start_id,
                    )

                    edge_usage[key] = (
                        edge_usage.get(
                            key,
                            0,
                        )
                        + 1
                    )

                continue

        accepted.append(
            route
        )

        for index, segment in enumerate(
            route.segments
        ):
            start_id = (
                route.node_ids[index]
                if index < len(route.node_ids)
                else None
            )

            key = _segment_key(
                segment,
                start_id,
            )

            edge_usage[key] = (
                edge_usage.get(
                    key,
                    0,
                )
                + 1
            )

    if not accepted:
        return []

    best = accepted[0]

    return [
        CandidateRoute(
            candidate_index=index,
            route=route,
            overlap_with_best=(
                1.0
                if index == 1
                else route_overlap(
                    route,
                    best,
                )
            ),
            diversity_penalty=(
                0.0
                if index == 1
                else diversity_penalty
            ),
        )
        for index, route in enumerate(
            accepted,
            start=1,
        )
    ]
