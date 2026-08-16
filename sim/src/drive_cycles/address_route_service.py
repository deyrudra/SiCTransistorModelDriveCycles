from __future__ import annotations

"""
Bridge between address search and the Stuttgart road graph.

Usage:
    result = plan_candidate_routes_from_addresses(
        network,
        "Schlossplatz 1, Stuttgart",
        "Mercedesstraße 100, Stuttgart",
        project_root=PROJECT_ROOT,
        candidate_count=3,
    )

The returned geocoder matches can be shown in the UI. By default the first
match for each explicit search is used.
"""

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

from drive_cycles.address_search import (
    AddressSearchResult,
    NominatimAddressSearch,
    default_stuttgart_searcher,
)
from drive_cycles.candidate_routes import (
    CandidateRoute,
    plan_candidate_routes,
)


EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class AddressRoutePlan:
    start_match: AddressSearchResult
    end_match: AddressSearchResult
    start_node_id: Any
    end_node_id: Any
    start_snap_distance_m: float
    end_snap_distance_m: float
    candidates: tuple[CandidateRoute, ...]


def _node_id(
    node: Any,
) -> Any:
    for name in (
        "id",
        "node_id",
        "osm_id",
    ):
        if hasattr(node, name):
            return getattr(
                node,
                name,
            )

    raise AttributeError(
        "Road node has no id/node_id/osm_id attribute."
    )


def _node_latlon(
    node: Any,
) -> tuple[float, float] | None:
    if not hasattr(
        node,
        "lat",
    ):
        return None

    lon = None

    for name in (
        "lon",
        "lng",
        "longitude",
    ):
        if hasattr(
            node,
            name,
        ):
            lon = getattr(
                node,
                name,
            )
            break

    if lon is None:
        return None

    try:
        return (
            float(node.lat),
            float(lon),
        )
    except (
        TypeError,
        ValueError,
    ):
        return None


def haversine_distance_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    phi1 = math.radians(
        lat1
    )
    phi2 = math.radians(
        lat2
    )

    dphi = math.radians(
        lat2 - lat1
    )

    dlambda = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(
            dphi / 2.0
        ) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(
            dlambda / 2.0
        ) ** 2
    )

    return (
        2.0
        * EARTH_RADIUS_M
        * math.asin(
            min(
                1.0,
                math.sqrt(a),
            )
        )
    )


def nearest_drivable_node_latlon(
    network: Any,
    latitude: float,
    longitude: float,
    *,
    maximum_distance_m: float = 1000.0,
) -> tuple[Any, float]:
    nodes = getattr(
        network,
        "nodes",
        None,
    )

    outgoing = getattr(
        network,
        "outgoing",
        None,
    )

    if not isinstance(
        nodes,
        dict,
    ):
        raise AttributeError(
            "Road network must expose nodes as a dict."
        )

    if outgoing is None:
        raise AttributeError(
            "Road network must expose outgoing adjacency."
        )

    best_node_id = None
    best_distance = math.inf

    # Prefer nodes that can actually start a directed route.
    candidate_ids = (
        outgoing.keys()
        if hasattr(
            outgoing,
            "keys",
        )
        else nodes.keys()
    )

    for node_id in candidate_ids:
        node = nodes.get(
            node_id
        )

        if node is None:
            continue

        latlon = _node_latlon(
            node
        )

        if latlon is None:
            continue

        distance = haversine_distance_m(
            latitude,
            longitude,
            latlon[0],
            latlon[1],
        )

        if distance < best_distance:
            best_distance = distance
            best_node_id = node_id

    if best_node_id is None:
        raise RuntimeError(
            "No road nodes with latitude/longitude are available."
        )

    if best_distance > maximum_distance_m:
        raise RuntimeError(
            "Nearest drivable road node is "
            f"{best_distance:.1f} m away, beyond the "
            f"{maximum_distance_m:.1f} m snap limit."
        )

    return (
        best_node_id,
        best_distance,
    )


def plan_candidate_routes_from_addresses(
    network: Any,
    start_query: str,
    end_query: str,
    *,
    project_root: str | Path,
    searcher: NominatimAddressSearch | None = None,
    start_result_index: int = 0,
    end_result_index: int = 0,
    search_limit: int = 5,
    candidate_count: int = 3,
    cost_mode: str = "time",
    diversity_penalty: float = 0.75,
    max_overlap: float = 0.90,
    maximum_snap_distance_m: float = 1000.0,
) -> AddressRoutePlan:
    if searcher is None:
        searcher = default_stuttgart_searcher(
            project_root
        )

    start_results = searcher.search(
        start_query,
        limit=search_limit,
    )

    if not start_results:
        raise RuntimeError(
            f"No Stuttgart address match found for: {start_query!r}"
        )

    end_results = searcher.search(
        end_query,
        limit=search_limit,
    )

    if not end_results:
        raise RuntimeError(
            f"No Stuttgart address match found for: {end_query!r}"
        )

    if not (
        0
        <= start_result_index
        < len(start_results)
    ):
        raise IndexError(
            "start_result_index is outside the returned search results."
        )

    if not (
        0
        <= end_result_index
        < len(end_results)
    ):
        raise IndexError(
            "end_result_index is outside the returned search results."
        )

    start_match = start_results[
        start_result_index
    ]

    end_match = end_results[
        end_result_index
    ]

    (
        start_node_id,
        start_snap_distance_m,
    ) = nearest_drivable_node_latlon(
        network,
        start_match.latitude,
        start_match.longitude,
        maximum_distance_m=maximum_snap_distance_m,
    )

    (
        end_node_id,
        end_snap_distance_m,
    ) = nearest_drivable_node_latlon(
        network,
        end_match.latitude,
        end_match.longitude,
        maximum_distance_m=maximum_snap_distance_m,
    )

    candidates = plan_candidate_routes(
        network,
        start_node_id,
        end_node_id,
        count=candidate_count,
        cost_mode=cost_mode,
        diversity_penalty=diversity_penalty,
        max_overlap=max_overlap,
    )

    if not candidates:
        raise RuntimeError(
            "No drivable candidate route was found between the "
            "snapped start/end nodes."
        )

    return AddressRoutePlan(
        start_match=start_match,
        end_match=end_match,
        start_node_id=start_node_id,
        end_node_id=end_node_id,
        start_snap_distance_m=start_snap_distance_m,
        end_snap_distance_m=end_snap_distance_m,
        candidates=tuple(
            candidates
        ),
    )
