from __future__ import annotations

"""
Asynchronous route elevation provider for Stuttgart drive-cycle generation.

Uses OpenTopoData's SRTM90m endpoint by default and stores a persistent local
cache so previously queried OSM nodes do not require another network request.

Only nodes explicitly requested by the drive-cycle route are queried.
"""

from concurrent.futures import Future, ThreadPoolExecutor
import json
import math
from pathlib import Path
import threading
from typing import Iterable, Optional
from urllib import request


class ElevationManager:
    def __init__(
        self,
        *,
        cache_path: str | Path,
        dataset: str = "srtm90m",
        api_base_url: str = "https://api.opentopodata.org/v1",
        batch_size: int = 90,
        timeout_s: float = 20.0,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.dataset = dataset
        self.api_url = f"{api_base_url.rstrip('/')}/{dataset}"
        self.batch_size = max(1, min(int(batch_size), 100))
        self.timeout_s = max(1.0, float(timeout_s))

        self._lock = threading.Lock()
        self._elevations: dict[int, float] = {}
        self._pending: set[int] = set()
        self._failed: set[int] = set()
        self._futures: list[Future] = []

        self.executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="route-elevation",
        )

        self.last_error = ""
        self.requests_started = 0
        self.points_downloaded = 0

        self._load_cache()

    def close(self) -> None:
        self.poll()
        self._save_cache()
        self.executor.shutdown(
            wait=False,
            cancel_futures=True,
        )

    # ------------------------------------------------------------------
    # Persistent cache
    # ------------------------------------------------------------------

    def _load_cache(self) -> None:
        if not self.cache_path.is_file():
            return

        try:
            payload = json.loads(
                self.cache_path.read_text(encoding="utf-8")
            )

            values = payload.get("nodes", payload)

            if not isinstance(values, dict):
                return

            loaded = {}

            for key, value in values.items():
                try:
                    node_id = int(key)
                    elevation = float(value)
                except (TypeError, ValueError):
                    continue

                if math.isfinite(elevation):
                    loaded[node_id] = elevation

            self._elevations.update(loaded)

        except Exception as exc:
            self.last_error = (
                f"Could not load elevation cache: {exc}"
            )

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            temp = self.cache_path.with_suffix(
                self.cache_path.suffix + ".tmp"
            )

            payload = {
                "dataset": self.dataset,
                "nodes": {
                    str(node_id): elevation
                    for node_id, elevation
                    in sorted(self._elevations.items())
                },
            }

            temp.write_text(
                json.dumps(
                    payload,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            temp.replace(self.cache_path)

        except Exception as exc:
            self.last_error = (
                f"Could not save elevation cache: {exc}"
            )

    # ------------------------------------------------------------------
    # Network requests
    # ------------------------------------------------------------------

    @staticmethod
    def _lat_lon(node) -> Optional[tuple[float, float]]:
        lat = getattr(node, "lat", None)
        lon = getattr(node, "lon", None)

        if lon is None:
            lon = getattr(node, "lng", None)

        if lat is None or lon is None:
            return None

        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return None

        if not (
            math.isfinite(lat)
            and math.isfinite(lon)
        ):
            return None

        return lat, lon

    def request_nodes(
        self,
        network,
        node_ids: Iterable[int],
    ) -> None:
        """
        Queue missing route nodes for asynchronous elevation retrieval.
        """

        batch: list[tuple[int, float, float]] = []

        for node_id in node_ids:
            node_id = int(node_id)

            with self._lock:
                if (
                    node_id in self._elevations
                    or node_id in self._pending
                ):
                    continue

            node = network.nodes.get(node_id)

            if node is None:
                self._failed.add(node_id)
                continue

            coords = self._lat_lon(node)

            if coords is None:
                self._failed.add(node_id)
                self.last_error = (
                    f"OSM node {node_id} has no latitude/longitude"
                )
                continue

            lat, lon = coords
            batch.append(
                (node_id, lat, lon)
            )

            if len(batch) >= self.batch_size:
                self._submit_batch(batch)
                batch = []

        if batch:
            self._submit_batch(batch)

    def _submit_batch(
        self,
        points: list[tuple[int, float, float]],
    ) -> None:
        points = list(points)

        with self._lock:
            self._pending.update(
                node_id
                for node_id, _, _ in points
            )

        future = self.executor.submit(
            self._fetch_batch,
            points,
        )

        self._futures.append(future)
        self.requests_started += 1

    def _fetch_batch(
        self,
        points: list[tuple[int, float, float]],
    ):
        locations = "|".join(
            f"{lat:.7f},{lon:.7f}"
            for _, lat, lon in points
        )

        body = json.dumps(
            {
                "locations": locations,
                "interpolation": "cubic",
            }
        ).encode("utf-8")

        req = request.Request(
            self.api_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "StuttgartDriveCycle/1.0",
            },
            method="POST",
        )

        with request.urlopen(
            req,
            timeout=self.timeout_s,
        ) as response:
            payload = json.loads(
                response.read().decode("utf-8")
            )

        if payload.get("status") != "OK":
            raise RuntimeError(
                f"Elevation API returned status "
                f"{payload.get('status')!r}"
            )

        results = payload.get("results", [])

        if len(results) != len(points):
            raise RuntimeError(
                "Elevation API returned a different number "
                "of results than requested."
            )

        output: dict[int, float] = {}

        for point, result in zip(points, results):
            node_id = point[0]
            elevation = result.get("elevation")

            if elevation is None:
                continue

            elevation = float(elevation)

            if math.isfinite(elevation):
                output[node_id] = elevation

        return points, output

    def poll(self) -> int:
        """
        Integrate completed background requests.
        Returns number of newly cached elevation points.
        """

        completed = []
        remaining = []

        for future in self._futures:
            if future.done():
                completed.append(future)
            else:
                remaining.append(future)

        self._futures = remaining

        added = 0

        for future in completed:
            try:
                points, values = future.result()

                with self._lock:
                    for node_id, _, _ in points:
                        self._pending.discard(node_id)

                    for node_id, elevation in values.items():
                        self._elevations[node_id] = elevation
                        self._failed.discard(node_id)

                missing = (
                    set(node_id for node_id, _, _ in points)
                    - set(values)
                )

                self._failed.update(missing)

                added += len(values)
                self.points_downloaded += len(values)

                if missing:
                    self.last_error = (
                        f"Elevation unavailable for "
                        f"{len(missing)} route node(s)"
                    )
                else:
                    self.last_error = ""

            except Exception as exc:
                self.last_error = (
                    f"Elevation request failed: {exc}"
                )

                # Pending IDs must be released on failure so a future route
                # selection can retry them.
                # Future exceptions do not expose the original list reliably,
                # so clear the current pending set. There is only one worker.
                with self._lock:
                    self._pending.clear()

        if added:
            self._save_cache()

        return added

    # ------------------------------------------------------------------
    # Elevation / grade access
    # ------------------------------------------------------------------

    def elevation_m(
        self,
        node_id: int,
    ) -> Optional[float]:
        return self._elevations.get(
            int(node_id)
        )

    def route_ready(
        self,
        node_ids: Iterable[int],
    ) -> bool:
        ids = tuple(int(n) for n in node_ids)

        return bool(ids) and all(
            node_id in self._elevations
            for node_id in ids
        )

    def route_progress(
        self,
        node_ids: Iterable[int],
    ) -> tuple[int, int]:
        ids = tuple(int(n) for n in node_ids)

        ready = sum(
            1
            for node_id in ids
            if node_id in self._elevations
        )

        return ready, len(ids)

    def segment_grade_deg(
        self,
        segment,
        network,
    ) -> Optional[float]:
        start_elevation = self.elevation_m(
            segment.u
        )
        end_elevation = self.elevation_m(
            segment.v
        )

        if (
            start_elevation is None
            or end_elevation is None
        ):
            return None

        length_m = max(
            float(segment.length),
            1e-6,
        )

        rise_m = (
            end_elevation
            - start_elevation
        )

        return math.degrees(
            math.atan2(
                rise_m,
                length_m,
            )
        )
