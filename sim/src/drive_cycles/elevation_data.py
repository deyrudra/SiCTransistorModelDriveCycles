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
        # De-duplicate before submitting jobs. This is especially important when
        # Route 1/2/3 share most of the same OSM nodes.
        unique_ids = dict.fromkeys(
            node_ids
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
            return all(
                self._node_key(
                    node_id
                ) in self._cache
                for node_id in node_ids
            )

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

        return math.degrees(
            math.atan2(
                z2 - z1,
                length_m,
            )
        )
