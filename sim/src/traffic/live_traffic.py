"""Asynchronous TomTom live traffic integration for the Stuttgart simulator.

Reads TOMTOM_API_KEY from the environment. Traffic data is kept in memory only.
The manager samples several points around the current camera and exposes a
confidence-weighted current/free-flow speed ratio to the simulation.
"""
from __future__ import annotations

import math
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

import requests

from chunks.projection import local_to_latlon

FLOW_SEGMENT_URL = (
    "https://api.tomtom.com/traffic/services/4/"
    "flowSegmentData/absolute/10/json"
)
DEFAULT_REFRESH_INTERVAL_S = 60.0
DEFAULT_SAMPLE_POINTS = 5


@dataclass(frozen=True)
class TrafficSnapshot:
    ratio: float
    current_speed_kph: float
    free_flow_speed_kph: float
    confidence: float
    samples: int
    fetched_at: float

    @property
    def speed_factor(self) -> float:
        # Avoid making the whole simulated area completely immobile from one
        # noisy sample while still allowing severe congestion to be represented.
        return max(0.12, min(1.0, self.ratio))

    @property
    def density_factor(self) -> float:
        # Congestion is not a vehicle count. This only nudges the number of
        # simulated vehicles upward when observed speeds are substantially low.
        congestion = 1.0 - max(0.0, min(1.0, self.ratio))
        return 0.75 + 1.75 * congestion


class TomTomTrafficManager:
    def __init__(
        self,
        api_key: Optional[str] = None,
        refresh_interval_s: float = DEFAULT_REFRESH_INTERVAL_S,
        sample_points: int = DEFAULT_SAMPLE_POINTS,
        timeout_s: float = 5.0,
    ) -> None:
        self.api_key = api_key or os.getenv("TOMTOM_API_KEY", "").strip()
        self.refresh_interval_s = max(30.0, float(refresh_interval_s))
        self.sample_points = max(1, int(sample_points))
        self.timeout_s = max(1.0, float(timeout_s))

        self.snapshot: Optional[TrafficSnapshot] = None
        self.last_error = ""
        self.last_request_at = 0.0
        self._future: Optional[Future] = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tomtom-traffic")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def busy(self) -> bool:
        return self._future is not None and not self._future.done()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def update(self, camera) -> Optional[TrafficSnapshot]:
        """Poll completed work and queue a refresh when one is due."""
        self._drain_future()
        if not self.enabled or self.busy:
            return self.snapshot

        now = time.monotonic()
        if self.snapshot is not None and now - self.last_request_at < self.refresh_interval_s:
            return self.snapshot

        viewport_w_m = camera.width / max(camera.pixels_per_meter, 1e-6)
        viewport_h_m = camera.height / max(camera.pixels_per_meter, 1e-6)
        request = (
            camera.center_x,
            camera.center_y,
            viewport_w_m,
            viewport_h_m,
        )
        self.last_request_at = now
        self._future = self._executor.submit(self._fetch_area, *request)
        return self.snapshot

    def _drain_future(self) -> None:
        if self._future is None or not self._future.done():
            return
        try:
            result = self._future.result()
            if result is not None:
                self.snapshot = result
                self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self._future = None

    def _sample_offsets(self, width_m: float, height_m: float):
        if self.sample_points == 1:
            return [(0.0, 0.0)]
        offsets = [(0.0, 0.0)]
        ring_count = self.sample_points - 1
        for i in range(ring_count):
            angle = 2.0 * math.pi * i / ring_count
            offsets.append((
                width_m * 0.32 * math.cos(angle),
                height_m * 0.32 * math.sin(angle),
            ))
        return offsets

    def _fetch_point(self, lat: float, lon: float):
        response = requests.get(
            FLOW_SEGMENT_URL,
            params={
                "key": self.api_key,
                "point": f"{lat:.7f},{lon:.7f}",
                "unit": "kmph",
            },
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        data = response.json()["flowSegmentData"]

        current = float(data["currentSpeed"])
        free_flow = float(data["freeFlowSpeed"])
        confidence = float(data.get("confidence", 1.0))
        road_closed = bool(data.get("roadClosure", False))

        if free_flow <= 0.0:
            return None
        if road_closed:
            current = 0.0
        return current, free_flow, max(0.0, min(1.0, confidence))

    def _fetch_area(self, center_x: float, center_y: float, width_m: float, height_m: float):
        samples = []
        errors = []

        for dx, dy in self._sample_offsets(width_m, height_m):
            lat, lon = local_to_latlon(center_x + dx, center_y + dy)
            try:
                result = self._fetch_point(lat, lon)
                if result is not None:
                    samples.append(result)
            except Exception as exc:
                errors.append(str(exc))

        if not samples:
            detail = errors[0] if errors else "no usable TomTom flow samples"
            raise RuntimeError(f"TomTom traffic refresh failed: {detail}")

        weights = [max(confidence, 0.1) for _, _, confidence in samples]
        total_weight = sum(weights)
        current_avg = sum(s[0] * w for s, w in zip(samples, weights)) / total_weight
        free_avg = sum(s[1] * w for s, w in zip(samples, weights)) / total_weight
        confidence_avg = sum(s[2] * w for s, w in zip(samples, weights)) / total_weight
        ratio = current_avg / free_avg if free_avg > 0.0 else 1.0

        return TrafficSnapshot(
            ratio=max(0.0, min(1.5, ratio)),
            current_speed_kph=current_avg,
            free_flow_speed_kph=free_avg,
            confidence=confidence_avg,
            samples=len(samples),   
            fetched_at=time.time(),
        )
