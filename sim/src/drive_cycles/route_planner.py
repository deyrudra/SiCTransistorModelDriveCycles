from __future__ import annotations

"""
A* route planning for the existing Stuttgart RoadNetwork.

The current project already represents roads as directed RoadSegment objects:

    segment.u -> segment.v

and RoadNetwork.outgoing maps each node id to the segments that may be driven
from that node. This module deliberately uses that existing representation
instead of introducing NetworkX or a second graph model.
"""

from dataclasses import dataclass
import heapq
import math
from typing import Optional


@dataclass(frozen=True)
class Route:
    """A planned route through the road network."""

    node_ids: tuple[int, ...]
    segments: tuple[object, ...]
    distance_m: float
    estimated_time_s: float


def _node_distance(network, a_id: int, b_id: int) -> float:
    a = network.nodes[a_id]
    b = network.nodes[b_id]
    return math.hypot(b.x - a.x, b.y - a.y)


def _segment_speed(segment, default_speed_mps: float = 10.0) -> float:
    speed = getattr(segment, "speed_limit", None)
    if speed is None or speed <= 0.0:
        return default_speed_mps
    return float(speed)


def _segment_cost(segment, cost_mode: str) -> float:
    length = max(0.0, float(getattr(segment, "length", 0.0)))
    if cost_mode == "distance":
        return length
    if cost_mode == "time":
        return length / max(_segment_speed(segment), 0.1)
    raise ValueError(
        f"Unknown route cost mode {cost_mode!r}; expected 'time' or 'distance'."
    )


def _heuristic(network, node_id: int, goal_id: int, cost_mode: str) -> float:
    distance = _node_distance(network, node_id, goal_id)
    if cost_mode == "distance":
        return distance
    return distance / 36.0


def plan_route(
    network,
    start_node_id: int,
    end_node_id: int,
    *,
    cost_mode: str = "time",
) -> Optional[Route]:
    """Plan a directed A* route through RoadNetwork.outgoing."""

    if start_node_id == end_node_id:
        return Route(
            node_ids=(start_node_id,),
            segments=(),
            distance_m=0.0,
            estimated_time_s=0.0,
        )

    if start_node_id not in network.nodes or end_node_id not in network.nodes:
        return None

    outgoing = getattr(network, "outgoing", {})
    open_heap: list[tuple[float, int, int]] = []
    push_counter = 0
    g_score: dict[int, float] = {start_node_id: 0.0}
    came_from: dict[int, tuple[int, object]] = {}

    heapq.heappush(
        open_heap,
        (
            _heuristic(network, start_node_id, end_node_id, cost_mode),
            push_counter,
            start_node_id,
        ),
    )

    closed: set[int] = set()

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        if current == end_node_id:
            break
        closed.add(current)

        for segment in outgoing.get(current, ()):
            next_node = segment.v
            if next_node not in network.nodes:
                continue

            tentative = g_score[current] + _segment_cost(segment, cost_mode)
            if tentative >= g_score.get(next_node, math.inf):
                continue

            g_score[next_node] = tentative
            came_from[next_node] = (current, segment)
            push_counter += 1
            priority = tentative + _heuristic(
                network, next_node, end_node_id, cost_mode
            )
            heapq.heappush(open_heap, (priority, push_counter, next_node))

    if end_node_id not in came_from:
        return None

    reverse_nodes = [end_node_id]
    reverse_segments = []
    current = end_node_id

    while current != start_node_id:
        previous, segment = came_from[current]
        reverse_segments.append(segment)
        reverse_nodes.append(previous)
        current = previous

    node_ids = tuple(reversed(reverse_nodes))
    segments = tuple(reversed(reverse_segments))

    distance_m = sum(
        max(0.0, float(getattr(segment, "length", 0.0)))
        for segment in segments
    )
    estimated_time_s = sum(
        max(0.0, float(getattr(segment, "length", 0.0)))
        / max(_segment_speed(segment), 0.1)
        for segment in segments
    )

    return Route(
        node_ids=node_ids,
        segments=segments,
        distance_m=distance_m,
        estimated_time_s=estimated_time_s,
    )


def nearest_road_node(
    network,
    x: float,
    y: float,
    *,
    max_distance_m: float = 35.0,
) -> Optional[int]:
    """Find the nearest node that participates in the drivable graph."""

    outgoing = getattr(network, "outgoing", {})
    best_id: Optional[int] = None
    best_distance_sq = max_distance_m * max_distance_m

    for node_id in outgoing:
        node = network.nodes.get(node_id)
        if node is None:
            continue

        dx = node.x - x
        dy = node.y - y
        distance_sq = dx * dx + dy * dy
        if distance_sq <= best_distance_sq:
            best_distance_sq = distance_sq
            best_id = node_id

    return best_id
