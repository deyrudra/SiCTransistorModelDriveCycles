from __future__ import annotations

"""
Local-first elevation manager.

Lookup priority:
    1. persistent JSON node cache
    2. local .hgt DEM tiles
    3. no public HTTP elevation API

This drop-in replacement preserves the interface used by visualization.py and
the headless parallel simulation code:
    request_nodes()
    poll()
    route_ready()
    route_progress()
    segment_grade_deg()
    close()
    dataset
    last_error

The goal is deterministic, rate-limit-free F5 route simulation.
"""

from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
)
import json
import math
from pathlib import Path
import threading
from typing import Any

from drive_cycles.local_dem import (
    LocalHgtDem,
    tile_name_for_latlon,
)

# Route elevation-profile conditioning.
#
# HGT/DEM values are sampled on a raster while OSM can contain road nodes only
# a few metres apart. Differencing adjacent node elevations therefore creates
# artificial sawtooth grades. We condition the elevation profile over distance
# before calculating road grade.
SMOOTHING_RADIUS_M = 25.0
GRADE_BASELINE_M = 25.0
SPIKE_REJECTION_THRESHOLD_M = 8.0
SPIKE_LOCAL_SPAN_MAX_M = 70.0

# 15 degrees ~= 26.8% road grade. This is already extremely steep for a normal
# routed passenger-car road, while being much less permissive than the old
# emergency +/-25 degree validator boundary.
MAX_ABS_ROAD_GRADE_DEG = 15.0

# Fallback only, used if a segment is queried before/without a prepared ordered
# route profile. It preserves downstream validator compatibility.
FALLBACK_MAX_ABS_GRADE_DEG = 25.0


class ElevationManager:
    def __init__(
        self,
        *,
        cache_path: str | Path,
        dem_dir: str | Path | None = None,
        max_workers: int = 2,
    ) -> None:
        self.cache_path = Path(
            cache_path
        )

        if dem_dir is None:
            dem_dir = (
                self.cache_path.parent
                / "dem"
            )

        self.dem_dir = Path(
            dem_dir
        )

        self.dem = LocalHgtDem(
            self.dem_dir
        )

        self.dataset = (
            "local_hgt+json_cache"
        )

        self.last_error = ""

        self._cache_lock = threading.RLock()

        self._cache = self._load_cache()

        self._executor = ThreadPoolExecutor(
            max_workers=max(
                1,
                int(max_workers),
            ),
            thread_name_prefix="local-elevation",
        )

        self._pending: dict[
            int,
            Future
        ] = {}

        self._dirty = False

        # Ordered route requests and route-conditioned grades.
        # request_nodes() already receives the ordered route node IDs, so no
        # external API change is needed.
        self._requested_routes: dict[
            tuple,
            tuple[object, tuple]
        ] = {}

        self._route_segment_grades: dict[
            tuple,
            float
        ] = {}

        self._route_profile_stats: dict[
            tuple,
            dict[str, float | int]
        ] = {}

    def close(self) -> None:
        self.poll()
        self._save_cache_if_needed()
        self._executor.shutdown(
            wait=False,
            cancel_futures=True,
        )
        self.dem.close()

    def _load_cache(
        self,
    ) -> dict[str, float]:
        if not self.cache_path.is_file():
            return {}

        try:
            with self.cache_path.open(
                "r",
                encoding="utf-8",
            ) as handle:
                raw = json.load(
                    handle
                )
        except (
            OSError,
            json.JSONDecodeError,
        ):
            return {}

        result = {}

        if isinstance(
            raw,
            dict,
        ):
            for key, value in raw.items():
                try:
                    result[
                        str(key)
                    ] = float(
                        value
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    continue

        return result

    def _save_cache_if_needed(
        self,
    ) -> None:
        if not self._dirty:
            return

        self.cache_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary = (
            self.cache_path
            .with_suffix(
                self.cache_path.suffix
                + ".tmp"
            )
        )

        with self._cache_lock:
            payload = dict(
                self._cache
            )

        with temporary.open(
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                payload,
                handle,
                indent=2,
                sort_keys=True,
            )

        temporary.replace(
            self.cache_path
        )

        self._dirty = False

    @staticmethod
    def _node_key(
        node_id: Any,
    ) -> str:
        return str(
            node_id
        )

    @staticmethod
    def _node_latlon(
        node,
    ) -> tuple[float, float]:
        latitude = float(
            node.lat
        )

        longitude = None

        for name in (
            "lon",
            "lng",
            "longitude",
        ):
            if hasattr(
                node,
                name,
            ):
                longitude = float(
                    getattr(
                        node,
                        name,
                    )
                )
                break

        if longitude is None:
            raise AttributeError(
                "Road node has no lon/lng/longitude attribute."
            )

        return (
            latitude,
            longitude,
        )

    def _lookup_node_worker(
        self,
        node_id: Any,
        latitude: float,
        longitude: float,
    ) -> tuple[
        Any,
        float | None,
        str | None,
    ]:
        try:
            elevation = self.dem.elevation_m(
                latitude,
                longitude,
            )
        except FileNotFoundError:
            tile = tile_name_for_latlon(
                latitude,
                longitude,
            )

            return (
                node_id,
                None,
                (
                    f"Local DEM tile {tile}.hgt is missing. "
                    "Run: python -m "
                    "drive_cycles.download_local_dem"
                ),
            )
        except Exception as exc:
            return (
                node_id,
                None,
                (
                    f"Local DEM lookup failed for "
                    f"node {node_id}: {exc}"
                ),
            )

        if elevation is None:
            return (
                node_id,
                None,
                (
                    f"DEM returned no elevation "
                    f"for node {node_id}"
                ),
            )

        return (
            node_id,
            float(elevation),
            None,
        )

    def request_nodes(
        self,
        network,
        node_ids,
    ) -> None:
        ordered_node_ids = tuple(node_ids)

        if len(ordered_node_ids) >= 2:
            self._requested_routes[
                ordered_node_ids
            ] = (
                network,
                ordered_node_ids,
            )

        # De-duplicate before submitting jobs. This is especially important when
        # Route 1/2/3 share most of the same OSM nodes.
        unique_ids = dict.fromkeys(
            ordered_node_ids
        )

        for node_id in unique_ids:
            key = self._node_key(
                node_id
            )

            with self._cache_lock:
                if key in self._cache:
                    continue

            if node_id in self._pending:
                continue

            node = network.nodes.get(
                node_id
            )

            if node is None:
                continue

            try:
                latitude, longitude = (
                    self._node_latlon(
                        node
                    )
                )
            except Exception as exc:
                self.last_error = (
                    f"Elevation coordinates missing "
                    f"for node {node_id}: {exc}"
                )
                continue

            self._pending[
                node_id
            ] = self._executor.submit(
                self._lookup_node_worker,
                node_id,
                latitude,
                longitude,
            )

    def poll(self) -> None:
        completed = [
            (
                node_id,
                future,
            )
            for node_id, future
            in self._pending.items()
            if future.done()
        ]

        for node_id, future in completed:
            self._pending.pop(
                node_id,
                None,
            )

            try:
                (
                    returned_id,
                    elevation,
                    error,
                ) = future.result()
            except Exception as exc:
                self.last_error = (
                    f"Local elevation worker failed: "
                    f"{exc}"
                )
                continue

            if error:
                self.last_error = error
                continue

            if elevation is None:
                continue

            key = self._node_key(
                returned_id
            )

            with self._cache_lock:
                self._cache[
                    key
                ] = float(
                    elevation
                )

            self._dirty = True
            self.last_error = ""

        if completed:
            self._save_cache_if_needed()

    def elevation_for_node(
        self,
        node_id: Any,
    ) -> float | None:
        key = self._node_key(
            node_id
        )

        with self._cache_lock:
            value = self._cache.get(
                key
            )

        return (
            None
            if value is None
            else float(value)
        )

    @staticmethod
    def _haversine_distance_m(
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
    ) -> float:
        radius_m = 6_371_000.0

        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)

        a = (
            math.sin(dphi / 2.0) ** 2
            + math.cos(phi1)
            * math.cos(phi2)
            * math.sin(dlambda / 2.0) ** 2
        )

        return (
            2.0
            * radius_m
            * math.atan2(
                math.sqrt(a),
                math.sqrt(max(0.0, 1.0 - a)),
            )
        )

    def _route_step_distance_m(
        self,
        network,
        u,
        v,
    ) -> float:
        # Prefer the road-network segment length when available.
        outgoing = getattr(
            network,
            "outgoing",
            {},
        )

        try:
            candidates = outgoing.get(
                u,
                [],
            )
        except AttributeError:
            candidates = []

        for segment in candidates:
            if getattr(segment, "v", None) != v:
                continue

            try:
                length_m = float(
                    getattr(
                        segment,
                        "length",
                        0.0,
                    )
                )
            except (
                TypeError,
                ValueError,
            ):
                length_m = 0.0

            if length_m > 0.05:
                return length_m

        # Robust fallback from node coordinates.
        node_u = network.nodes.get(u)
        node_v = network.nodes.get(v)

        if node_u is None or node_v is None:
            return 0.0

        lat1, lon1 = self._node_latlon(node_u)
        lat2, lon2 = self._node_latlon(node_v)

        return self._haversine_distance_m(
            lat1,
            lon1,
            lat2,
            lon2,
        )

    @staticmethod
    def _interpolate_profile(
        distance_m: float,
        cumulative_m: list[float],
        elevations_m: list[float],
    ) -> float:
        if not cumulative_m:
            return 0.0

        if distance_m <= cumulative_m[0]:
            return elevations_m[0]

        if distance_m >= cumulative_m[-1]:
            return elevations_m[-1]

        lo = 0
        hi = len(cumulative_m) - 1

        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if cumulative_m[mid] <= distance_m:
                lo = mid
            else:
                hi = mid

        s0 = cumulative_m[lo]
        s1 = cumulative_m[hi]

        if s1 <= s0 + 1e-12:
            return elevations_m[lo]

        fraction = (
            (distance_m - s0)
            / (s1 - s0)
        )

        return (
            elevations_m[lo]
            + fraction
            * (
                elevations_m[hi]
                - elevations_m[lo]
            )
        )

    @staticmethod
    def _reject_isolated_spikes(
        cumulative_m: list[float],
        elevations_m: list[float],
    ) -> tuple[list[float], int]:
        if len(elevations_m) < 3:
            return list(elevations_m), 0

        filtered = list(elevations_m)
        rejected = 0

        for i in range(
            1,
            len(elevations_m) - 1,
        ):
            left_span = (
                cumulative_m[i]
                - cumulative_m[i - 1]
            )
            right_span = (
                cumulative_m[i + 1]
                - cumulative_m[i]
            )
            local_span = (
                left_span
                + right_span
            )

            if (
                left_span <= 0.0
                or right_span <= 0.0
                or local_span > SPIKE_LOCAL_SPAN_MAX_M
            ):
                continue

            z_left = elevations_m[i - 1]
            z_mid = elevations_m[i]
            z_right = elevations_m[i + 1]

            left_jump = z_mid - z_left
            right_jump = z_right - z_mid

            # Only remove an isolated peak/trough: the two adjacent jumps must
            # reverse direction. A genuine sustained climb/descend is retained.
            if left_jump * right_jump >= 0.0:
                continue

            expected = (
                z_left
                + (
                    z_right - z_left
                )
                * (
                    left_span
                    / local_span
                )
            )

            if (
                abs(z_mid - expected)
                >= SPIKE_REJECTION_THRESHOLD_M
            ):
                filtered[i] = expected
                rejected += 1

        return filtered, rejected

    @staticmethod
    def _smooth_elevations_by_distance(
        cumulative_m: list[float],
        elevations_m: list[float],
    ) -> list[float]:
        if len(elevations_m) <= 2:
            return list(elevations_m)

        smoothed: list[float] = []

        for i, center_s in enumerate(cumulative_m):
            weighted_sum = 0.0
            weight_sum = 0.0

            for sample_s, sample_z in zip(
                cumulative_m,
                elevations_m,
            ):
                distance = abs(
                    sample_s - center_s
                )

                if distance > SMOOTHING_RADIUS_M:
                    continue

                # Triangular distance weighting. A tiny floor avoids making a
                # point exactly at the radius numerically irrelevant.
                weight = max(
                    0.02,
                    1.0
                    - distance
                    / SMOOTHING_RADIUS_M,
                )

                weighted_sum += (
                    sample_z
                    * weight
                )
                weight_sum += weight

            if weight_sum <= 0.0:
                smoothed.append(
                    elevations_m[i]
                )
            else:
                smoothed.append(
                    weighted_sum
                    / weight_sum
                )

        # Preserve the actual endpoint elevations and therefore the genuine net
        # route climb/descent. Only the interior profile is smoothed.
        smoothed[0] = elevations_m[0]
        smoothed[-1] = elevations_m[-1]

        return smoothed

    def _build_route_profile_if_ready(
        self,
        route_key: tuple,
    ) -> bool:
        request = self._requested_routes.get(
            route_key
        )

        if request is None:
            return False

        network, node_ids = request

        if len(node_ids) < 2:
            return False

        elevations: list[float] = []

        for node_id in node_ids:
            elevation = self.elevation_for_node(
                node_id
            )

            if elevation is None:
                return False

            elevations.append(
                float(elevation)
            )

        cumulative_m = [
            0.0
        ]

        for u, v in zip(
            node_ids[:-1],
            node_ids[1:],
        ):
            step = self._route_step_distance_m(
                network,
                u,
                v,
            )

            # Avoid zero-width profile intervals while keeping the correction
            # negligible relative to a real road segment.
            step = max(
                0.05,
                float(step),
            )

            cumulative_m.append(
                cumulative_m[-1]
                + step
            )

        filtered, rejected_spikes = (
            self._reject_isolated_spikes(
                cumulative_m,
                elevations,
            )
        )

        smoothed = (
            self._smooth_elevations_by_distance(
                cumulative_m,
                filtered,
            )
        )

        half_baseline = (
            0.5
            * GRADE_BASELINE_M
        )

        clamp_count = 0
        raw_max = -float("inf")
        raw_min = float("inf")

        for i, (
            u,
            v,
        ) in enumerate(
            zip(
                node_ids[:-1],
                node_ids[1:],
            )
        ):
            midpoint = (
                0.5
                * (
                    cumulative_m[i]
                    + cumulative_m[i + 1]
                )
            )

            left_s = max(
                cumulative_m[0],
                midpoint - half_baseline,
            )
            right_s = min(
                cumulative_m[-1],
                midpoint + half_baseline,
            )

            baseline = (
                right_s
                - left_s
            )

            if baseline <= 1e-9:
                grade_deg = 0.0
            else:
                left_z = self._interpolate_profile(
                    left_s,
                    cumulative_m,
                    smoothed,
                )
                right_z = self._interpolate_profile(
                    right_s,
                    cumulative_m,
                    smoothed,
                )

                raw_grade_deg = math.degrees(
                    math.atan2(
                        right_z - left_z,
                        baseline,
                    )
                )

                raw_max = max(
                    raw_max,
                    raw_grade_deg,
                )
                raw_min = min(
                    raw_min,
                    raw_grade_deg,
                )

                grade_deg = max(
                    -MAX_ABS_ROAD_GRADE_DEG,
                    min(
                        MAX_ABS_ROAD_GRADE_DEG,
                        raw_grade_deg,
                    ),
                )

                if (
                    abs(raw_grade_deg)
                    > MAX_ABS_ROAD_GRADE_DEG
                ):
                    clamp_count += 1

            self._route_segment_grades[
                (
                    u,
                    v,
                )
            ] = float(
                grade_deg
            )

        self._route_profile_stats[
            route_key
        ] = {
            "route_distance_m": (
                cumulative_m[-1]
            ),
            "raw_start_elevation_m": (
                elevations[0]
            ),
            "raw_end_elevation_m": (
                elevations[-1]
            ),
            "net_elevation_change_m": (
                elevations[-1]
                - elevations[0]
            ),
            "isolated_spikes_rejected": (
                rejected_spikes
            ),
            "grade_clamp_segment_count": (
                clamp_count
            ),
            "raw_max_grade_deg": (
                0.0
                if raw_max == -float("inf")
                else raw_max
            ),
            "raw_min_grade_deg": (
                0.0
                if raw_min == float("inf")
                else raw_min
            ),
            "smoothing_radius_m": (
                SMOOTHING_RADIUS_M
            ),
            "grade_baseline_m": (
                GRADE_BASELINE_M
            ),
        }

        return True

    def route_profile_stats(
        self,
        node_ids,
    ) -> dict[str, float | int]:
        route_key = tuple(
            node_ids
        )

        self._build_route_profile_if_ready(
            route_key
        )

        return dict(
            self._route_profile_stats.get(
                route_key,
                {},
            )
        )

    def route_ready(
        self,
        node_ids,
    ) -> bool:
        node_ids = list(
            dict.fromkeys(
                node_ids
            )
        )

        if not node_ids:
            return True

        with self._cache_lock:
            ready = all(
                self._node_key(
                    node_id
                ) in self._cache
                for node_id in node_ids
            )

        if ready:
            self._build_route_profile_if_ready(
                tuple(node_ids)
            )

        return ready

    def route_progress(
        self,
        node_ids,
    ) -> tuple[int, int]:
        unique = list(
            dict.fromkeys(
                node_ids
            )
        )

        with self._cache_lock:
            ready = sum(
                1
                for node_id in unique
                if self._node_key(
                    node_id
                ) in self._cache
            )

        return (
            ready,
            len(unique),
        )

    def segment_grade_deg(
        self,
        segment,
        network,
    ) -> float | None:
        route_grade = (
            self._route_segment_grades.get(
                (
                    segment.u,
                    segment.v,
                )
            )
        )

        if route_grade is not None:
            return float(
                route_grade
            )

        # If the route profile was not prepared yet, retain a safe legacy
        # fallback rather than failing the simulation.
        z1 = self.elevation_for_node(
            segment.u
        )
        z2 = self.elevation_for_node(
            segment.v
        )

        if (
            z1 is None
            or z2 is None
        ):
            return None

        length_m = float(
            getattr(
                segment,
                "length",
                0.0,
            )
        )

        if length_m <= 1e-9:
            return 0.0

        raw_grade_deg = math.degrees(
            math.atan2(
                z2 - z1,
                length_m,
            )
        )

        return max(
            -FALLBACK_MAX_ABS_GRADE_DEG,
            min(
                FALLBACK_MAX_ABS_GRADE_DEG,
                raw_grade_deg,
            ),
        )
