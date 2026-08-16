"""High-performance streaming Stuttgart/OpenStreetMap Pygame traffic visualizer.

Instead of inventing roads procedurally, this viewer treats the existing 250 m
chunk grid as a streaming/cache layer over real OpenStreetMap data. As the
camera pans, visible chunks are read from the project's existing OSM chunk
cache/downloader pipeline, merged into RoadNetwork, and drawn
with street names and city-map context.

Run from your project root:
    python stuttgart_exact_get_chunk_v4.py
    python stuttgart_exact_get_chunk_v4.py --vehicles 50
    python stuttgart_exact_get_chunk_v4.py --offline

Requires:
    pip install pygame pyproj shapely

Controls:
    WASD / arrows   pan
    mouse drag      pan
    mouse wheel     zoom
    Space           pause/resume
    + / -           simulation speed
    V               add 10 cars
    L               toggle street labels
    B               toggle buildings/landuse
    G               toggle chunk grid
    H               toggle HUD
    R               return to Stuttgart origin
    Esc             quit
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
import xml.etree.ElementTree as ET
import importlib
import importlib.util
import inspect
import ast
import queue
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pygame

# -----------------------------------------------------------------------------
# Project imports
# -----------------------------------------------------------------------------
SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))

import traffic.road_network as road_network_module
from traffic.road_network import RoadNetwork, RoadSegment
from traffic.traffic_light import TrafficLight
from traffic.intersection import Intersection
from traffic.simulation import Simulation
from traffic.vehicle import Vehicle
from chunks.grid import CHUNK_SIZE, world_to_chunk, chunk_bounds
from chunks.projection import local_bounds_to_latlon, latlon_to_local
from traffic.live_traffic import TomTomTrafficManager
from drive_cycles.route_planner import Route, nearest_road_node, plan_route
from drive_cycles.ego_vehicle import EgoVehicle
from drive_cycles.drive_cycle_recorder import DriveCycleRecorder
from drive_cycles.vehicle_config import VehicleDynamicsConfig, load_vehicle_config
from drive_cycles.elevation_data import ElevationManager
from drive_cycles.candidate_routes import CandidateRoute, plan_candidate_routes
from drive_cycles.address_search import default_stuttgart_searcher
from drive_cycles.address_route_service import nearest_drivable_node_latlon
from drive_cycles.route_gui import RoutePlannerPanel, GuiAction

# Anchor project/cache discovery to the module that already owns the cache
# convention, not to wherever this viewer script happens to be copied.
ROAD_NETWORK_FILE = Path(road_network_module.__file__).resolve()
PROJECT_ROOT = ROAD_NETWORK_FILE.parents[2]
PROJECT_SRC = PROJECT_ROOT / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))
DEFAULT_CHUNK_CACHE = PROJECT_ROOT / "cache" / "osm_chunks" / "stuttgart"
DEFAULT_DRIVE_CYCLE_DIR = PROJECT_ROOT / "cycles"
DEFAULT_CAR_CONFIG = PROJECT_ROOT / "src" / "drive_cycles" / "car_configs" / "tesla_model3_lr_rwd.yaml"
DEFAULT_ELEVATION_CACHE = PROJECT_ROOT / "cache" / "elevation" / "stuttgart_srtm90m.json"

# -----------------------------------------------------------------------------
# Appearance -- map-like rather than debug-grid-like
# -----------------------------------------------------------------------------
BACKGROUND = (231, 228, 219)
BUILDING = (208, 199, 188)
BUILDING_EDGE = (190, 181, 170)
PARK = (199, 218, 190)
WATER = (173, 205, 220)
RAIL = (148, 137, 132)
ROAD_CASING = (205, 201, 192)
ROAD_LOCAL = (250, 249, 244)
ROAD_TERTIARY = (246, 242, 225)
ROAD_SECONDARY = (244, 226, 177)
ROAD_PRIMARY = (239, 196, 153)
ROAD_TRUNK = (231, 172, 144)
ROAD_MOTORWAY = (222, 146, 154)
ROAD_CENTER = (232, 225, 208)
STREET_TEXT = (75, 72, 67)
STREET_HALO = (246, 244, 237)
VEHICLE = (38, 112, 187)
VEHICLE_EDGE = (244, 248, 252)
GREEN = (47, 166, 83)
YELLOW = (229, 174, 28)
RED = (208, 61, 61)
UNKNOWN_LIGHT = (120, 120, 120)
GRID = (160, 157, 150)
HUD_BG = (30, 32, 34, 218)
HUD_TEXT = (244, 246, 247)
HUD_MUTED = (185, 190, 194)
ROUTE_LINE = (36, 94, 190)
ROUTE_START = (40, 170, 80)
ROUTE_END = (210, 65, 65)
ROUTE_MARKER_EDGE = (250, 250, 250)
ROUTE_CANDIDATE_COLORS = (
    (36, 94, 190),
    (140, 83, 190),
    (35, 145, 135),
    (202, 122, 36),
    (170, 72, 92),
)
ROUTE_CANDIDATE_MUTED = (120, 125, 132)
ADDRESS_CORRIDOR_RADIUS_CHUNKS = 1
ADDRESS_PLAN_TIMEOUT_S = 60.0
EGO_VEHICLE = (255, 80, 20)
EGO_VEHICLE_EDGE = (255, 255, 255)

CHUNK_MARGIN = 1
MIN_ZOOM = 0.15
MAX_ZOOM = 25.0
DEFAULT_PPM = 2.2

# The viewer never talks to Overpass directly. Missing chunks are handed to
# the project's project get_chunk cache/downloader (when one is discoverable/configured) and
# all data is consumed through the shared cache directory.


DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service",
}

# -----------------------------------------------------------------------------
# Visual feature containers
# -----------------------------------------------------------------------------
@dataclass
class PolygonFeature:
    points: list[tuple[float, float]]
    kind: str


@dataclass
class LineFeature:
    points: list[tuple[float, float]]
    kind: str


@dataclass
class ChunkVisuals:
    polygons: list[PolygonFeature] = field(default_factory=list)
    lines: list[LineFeature] = field(default_factory=list)


@dataclass
class Camera:
    width: int
    height: int
    center_x: float = 0.0
    center_y: float = 0.0
    pixels_per_meter: float = DEFAULT_PPM

    def world_to_screen(self, x: float, y: float) -> tuple[int, int]:
        sx = self.width * 0.5 + (x - self.center_x) * self.pixels_per_meter
        sy = self.height * 0.5 - (y - self.center_y) * self.pixels_per_meter
        return int(round(sx)), int(round(sy))

    def screen_to_world(self, sx: float, sy: float) -> tuple[float, float]:
        x = self.center_x + (sx - self.width * 0.5) / self.pixels_per_meter
        y = self.center_y - (sy - self.height * 0.5) / self.pixels_per_meter
        return x, y

    def pan_pixels(self, dx: float, dy: float) -> None:
        self.center_x -= dx / self.pixels_per_meter
        self.center_y += dy / self.pixels_per_meter

    def zoom_at(self, factor: float, screen_pos: tuple[int, int]) -> None:
        before = self.screen_to_world(*screen_pos)
        self.pixels_per_meter = max(MIN_ZOOM, min(MAX_ZOOM, self.pixels_per_meter * factor))
        after = self.screen_to_world(*screen_pos)
        self.center_x += before[0] - after[0]
        self.center_y += before[1] - after[1]

    def resize(self, w: int, h: int) -> None:
        self.width, self.height = w, h


# -----------------------------------------------------------------------------
# Real OSM chunk streaming
# -----------------------------------------------------------------------------
class ExistingChunkDownloader:
    """Use the project's actual simtools/osm/download_chunk.py contract.

    This adapter intentionally does *not* guess downloader signatures.  The
    supplied project logic defines the authoritative API:

        get_chunk(cx, cy) -> Path
        chunk_path(cx, cy) -> Path

    get_chunk() owns cache checking, downloading, saving, and the returned
    filename.  The viewer merely invokes it from a worker thread so Pygame
    never blocks while network I/O is happening.
    """

    def __init__(self, cache_dir: Path, spec: Optional[str] = None) -> None:
        self.cache_dir = Path(cache_dir)
        self.spec = spec
        self.module = None
        self.func = None
        self.chunk_path_func = None
        self.name = "NOT FOUND"
        self.error = ""
        self.discovery_notes = []
        self._discover(spec)

        # The downloader module is authoritative about where it stores chunks.
        if self.chunk_path_func is not None:
            try:
                self.cache_dir = Path(self.chunk_path_func(0, 0)).resolve().parent
            except Exception as exc:
                self.discovery_notes.append(f"chunk_path probe failed: {exc}")

    def _bind_module(self, module, display_name: str) -> bool:
        get_chunk = getattr(module, "get_chunk", None)
        chunk_path_func = getattr(module, "chunk_path", None)
        if not callable(get_chunk):
            return False
        if not callable(chunk_path_func):
            self.discovery_notes.append(f"{display_name}: missing chunk_path()")
            return False
        self.module = module
        self.func = get_chunk
        self.chunk_path_func = chunk_path_func
        self.name = f"{display_name}:get_chunk"
        self.error = ""
        return True

    def _import_module(self, module_name: str) -> bool:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            self.discovery_notes.append(f"{module_name}: {type(exc).__name__}: {exc}")
            return False
        return self._bind_module(module, module_name)

    def _load_file(self, path: Path) -> bool:
        path = Path(path).resolve()
        module_name = f"_pygame_exact_chunk_{abs(hash(str(path)))}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return False
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            self.discovery_notes.append(f"{path}: {type(exc).__name__}: {exc}")
            return False
        try:
            display = str(path.relative_to(Path.cwd()))
        except Exception:
            display = str(path)
        return self._bind_module(module, display)

    def _discover(self, explicit: Optional[str]) -> None:
        # Explicit form can be either a module, module:get_chunk, or a .py path.
        if explicit:
            if explicit.endswith(".py"):
                if self._load_file(Path(explicit)):
                    return
            else:
                module_name = explicit.split(":", 1)[0]
                if self._import_module(module_name):
                    return
            self.error = f"Could not load exact get_chunk API from {explicit}"
            return

        # This is the location shown by the user's project/status overlay.
        for module_name in (
            "simtools.osm.download_chunk",
            "osm.download_chunk",
            "download_chunk",
        ):
            if self._import_module(module_name):
                return

        # Fall back to finding exactly a file named download_chunk.py which
        # contains both get_chunk() and chunk_path(). No fuzzy downloader scan.
        roots = []
        for candidate in (Path.cwd(), PROJECT_ROOT, PROJECT_SRC, Path(__file__).resolve().parent):
            try:
                candidate = candidate.resolve()
            except Exception:
                continue
            if candidate.exists() and candidate not in roots:
                roots.append(candidate)

        ranked = []
        for root in roots:
            candidates = [
                root / "simtools" / "osm" / "download_chunk.py",
                root / "src" / "simtools" / "osm" / "download_chunk.py",
                root / "download_chunk.py",
            ]
            for candidate in candidates:
                if candidate.exists():
                    ranked.append(candidate)
            # Limited recursive fallback for project-local download_chunk.py.
            try:
                for candidate in root.glob("*/osm/download_chunk.py"):
                    if candidate.exists():
                        ranked.append(candidate)
                for candidate in root.glob("*/*/osm/download_chunk.py"):
                    if candidate.exists():
                        ranked.append(candidate)
            except OSError:
                pass

        seen = set()
        for path in ranked:
            try:
                rp = path.resolve()
            except Exception:
                continue
            if rp in seen:
                continue
            seen.add(rp)
            if self._load_file(rp):
                return

        self.error = (
            "Could not find simtools/osm/download_chunk.py with get_chunk() "
            "and chunk_path(). Use --downloader simtools.osm.download_chunk"
        )

    @property
    def available(self) -> bool:
        return self.func is not None and self.chunk_path_func is not None

    def path_for(self, cx: int, cy: int) -> Path:
        if self.chunk_path_func is None:
            return self.cache_dir / f"chunk_{cx}_{cy}.osm"
        return Path(self.chunk_path_func(cx, cy)).resolve()

    def request(self, cx: int, cy: int) -> Path:
        """Call the exact project get_chunk(cx, cy) API and return its file."""
        if not self.available:
            raise RuntimeError(self.error or "No get_chunk() downloader is available")
        result = self.func(cx, cy)
        # The supplied implementation returns the filename. Be tolerant of a
        # None return only if chunk_path() confirms the file was written.
        path = Path(result).resolve() if result is not None else self.path_for(cx, cy)
        if not path.exists():
            fallback = self.path_for(cx, cy)
            if fallback.exists():
                path = fallback
            else:
                raise FileNotFoundError(
                    f"get_chunk({cx}, {cy}) returned but no cache file exists: {path}"
                )
        return path


class OSMChunkManager:
    """Non-blocking consumer of the project's existing Stuttgart chunk cache."""

    def __init__(self, network, simulation, cache_dir: Path,
                 offline: bool = False, downloader_spec: Optional[str] = None) -> None:
        self.network = network
        self.simulation = simulation
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline

        self.network.segments = []
        self.network.outgoing = {}
        self.loaded_chunks = set()
        self.queued_chunks = set()
        self.pending_chunks = set()
        self.requested_chunks = set()
        self.visuals = {}
        self.road_ids_by_chunk = {}
        self.revision = 0
        self.generated_last_update = 0
        self.downloads_last_update = 0
        self.last_error = ""

        # User-facing streaming/download status. These are deliberately updated
        # on the main thread so the Pygame overlay can read them safely.
        self.request_started = {}
        self.status_events = deque(maxlen=8)
        self.total_download_requests = 0
        self.total_downloaded_chunks = 0
        self.total_cache_chunks = 0

        # Parsing cached XML and invoking the user's downloader never blocks the
        # Pygame thread. One downloader worker is intentional: the downloader's
        # own queue/rate limiting stays authoritative and we don't create bursts.
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chunk-cache")
        self.completed = queue.Queue()
        self.downloader = ExistingChunkDownloader(cache_dir, downloader_spec)
        # Use download_chunk.py's own chunk_path() location as the source of truth.
        self.cache_dir = self.downloader.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_pending = 4
        self.max_merges_per_frame = 1
        self.retry_after = {}
        self.cache_poll_interval = 0.25
        self._next_cache_poll = 0.0

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def chunks_for_view(self, camera: Camera):
        p1 = camera.screen_to_world(0, 0)
        p2 = camera.screen_to_world(camera.width, camera.height)
        min_x, max_x = sorted((p1[0], p2[0]))
        min_y, max_y = sorted((p1[1], p2[1]))
        min_cx, min_cy = world_to_chunk(min_x, min_y)
        max_cx, max_cy = world_to_chunk(max_x, max_y)
        result = [(cx, cy)
                  for cx in range(min_cx - CHUNK_MARGIN, max_cx + CHUNK_MARGIN + 1)
                  for cy in range(min_cy - CHUNK_MARGIN, max_cy + CHUNK_MARGIN + 1)]
        center = world_to_chunk(camera.center_x, camera.center_y)
        result.sort(key=lambda c: abs(c[0]-center[0]) + abs(c[1]-center[1]))
        return result

    def chunk_path(self, cx: int, cy: int) -> Path:
        # download_chunk.py owns the path convention.
        return self.downloader.path_for(cx, cy)

    def _cache_ready_worker(self, key) -> None:
        """Validate a cached chunk off-thread, then queue it for integration."""
        path = self.chunk_path(*key)
        try:
            ET.parse(path)
            self.completed.put((key, "cache", ""))
        except Exception as exc:
            self.completed.put((key, "cache", f"Invalid cached chunk {path.name}: {exc}"))

    def _downloader_worker(self, key) -> None:
        """Run the supplied get_chunk(cx, cy) off the Pygame thread."""
        cx, cy = key
        try:
            path = self.chunk_path(cx, cy)
            if path.exists():
                ET.parse(path)
                self.completed.put((key, "cache", ""))
                return

            # This is the exact supplied cache/download/save logic.
            path = self.downloader.request(cx, cy)
            ET.parse(path)
            self.completed.put((key, "downloader", ""))
        except Exception as exc:
            self.completed.put((key, "downloader", f"get_chunk failed for {cx},{cy}: {exc}"))

    def request_chunk(self, key) -> bool:
        if key in self.loaded_chunks or key in self.queued_chunks or key in self.pending_chunks:
            return False
        if time.monotonic() < self.retry_after.get(key, 0.0):
            return False

        path = self.chunk_path(*key)
        self.queued_chunks.add(key)
        if path.exists():
            self.executor.submit(self._cache_ready_worker, key)
            return True

        self.queued_chunks.discard(key)
        if self.offline:
            return False

        # Hand the request to the user's project get_chunk cache/downloader exactly once. If no
        # adapter is available, do not make a replacement HTTP request; simply
        # watch the shared cache for another part of the project to fill it.
        if self.downloader.available and key not in self.requested_chunks:
            if len(self.pending_chunks) >= self.max_pending:
                return False
            self.requested_chunks.add(key)
            self.pending_chunks.add(key)
            self.request_started[key] = time.monotonic()
            self.total_download_requests += 1
            self.status_events.appendleft((time.monotonic(), f"Requested {key[0]},{key[1]}"))
            self.executor.submit(self._downloader_worker, key)
            return True
        return False

    def _poll_cache(self, wanted_keys) -> None:
        now = time.monotonic()
        if now < self._next_cache_poll:
            return
        self._next_cache_poll = now + self.cache_poll_interval
        for key in wanted_keys:
            if key in self.loaded_chunks or key in self.queued_chunks:
                continue
            if self.chunk_path(*key).exists():
                self.queued_chunks.add(key)
                self.executor.submit(self._cache_ready_worker, key)

    def parse_visuals(self, root):
        result = ChunkVisuals()
        for way in root.findall("way"):
            tags = {t.attrib.get("k"): t.attrib.get("v", "") for t in way.findall("tag")}
            refs = [int(nd.attrib["ref"]) for nd in way.findall("nd") if "ref" in nd.attrib]
            points = [(self.network.nodes[r].x, self.network.nodes[r].y)
                      for r in refs if r in self.network.nodes]
            if len(points) < 2:
                continue
            closed = len(points) >= 4 and refs and refs[0] == refs[-1]
            if "building" in tags and closed:
                result.polygons.append(PolygonFeature(points, "building"))
            elif tags.get("leisure") == "park" and closed:
                result.polygons.append(PolygonFeature(points, "park"))
            elif tags.get("landuse") in {"grass","meadow","forest","recreation_ground","cemetery"} and closed:
                result.polygons.append(PolygonFeature(points, "park"))
            elif tags.get("natural") == "water" and closed:
                result.polygons.append(PolygonFeature(points, "water"))
            elif "waterway" in tags:
                result.lines.append(LineFeature(points, "water"))
            elif "railway" in tags:
                result.lines.append(LineFeature(points, "rail"))
        return result

    def _append_road_segments(self, road_ids) -> None:
        for road_id in road_ids:
            road = self.network.roads.get(road_id)
            if road is None:
                continue
            for i in range(len(road.nodes)-1):
                u, v = road.nodes[i], road.nodes[i+1]
                if u not in self.network.nodes or v not in self.network.nodes:
                    continue
                pairs = [(u, v)] + ([] if road.oneway else [(v, u)])
                for a, b in pairs:
                    seg = RoadSegment(a, b, road)
                    na, nb = self.network.nodes[a], self.network.nodes[b]
                    seg.length = math.hypot(nb.x-na.x, nb.y-na.y)
                    self.network.segments.append(seg)
                    self.network.outgoing.setdefault(a, []).append(seg)

    def _rebuild_intersections_fast(self) -> None:
        self.network.traffic_lights = {
            n.id: self.network.traffic_lights.get(n.id, TrafficLight(n.id))
            for n in self.network.nodes.values() if n.traffic_light
        }
        incoming = defaultdict(list)
        for seg in self.network.segments:
            incoming[seg.v].append(seg)
        signals = [n for n in self.network.nodes.values() if n.traffic_light]
        cell = 40.0
        buckets = defaultdict(list)
        for n in signals:
            buckets[(math.floor(n.x/cell), math.floor(n.y/cell))].append(n)
        used = set()
        intersections = []
        for node in signals:
            if node.id in used:
                continue
            inter = Intersection(len(intersections))
            bx, by = math.floor(node.x/cell), math.floor(node.y/cell)
            nearby = []
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    nearby.extend(buckets.get((bx+dx, by+dy), ()))
            for other in nearby:
                if other.id not in used and math.hypot(other.x-node.x, other.y-node.y) <= 40.0:
                    inter.signal_nodes.append(other.id)
                    used.add(other.id)
            angles = []
            for node_id in inter.signal_nodes:
                inc = incoming.get(node_id)
                if inc:
                    seg = inc[0]
                    a, b = self.network.nodes[seg.u], self.network.nodes[seg.v]
                    angles.append((node_id, math.atan2(b.y-a.y, b.x-a.x)))
            if angles:
                ref = angles[0][1]
                for node_id, angle in angles:
                    diff = abs(ref-angle)
                    while diff > math.pi:
                        diff -= math.pi
                    (inter.phase_a if abs(diff) < math.radians(45) else inter.phase_b).append(node_id)
            if inter.phase_a or inter.phase_b:
                intersections.append(inter)
        self.network.intersections = intersections

    def merge_osm_file(self, path: Path, chunk_key) -> None:
        root = ET.parse(path).getroot()
        old_ids = set(self.network.roads)
        self.network.load_nodes(root)
        self.network.load_roads(root)
        self._append_road_segments(set(self.network.roads) - old_ids)
        road_ids = set()
        for way in root.findall("way"):
            tags = {t.attrib.get("k"): t.attrib.get("v", "") for t in way.findall("tag")}
            if tags.get("highway") in DRIVABLE:
                try:
                    road_ids.add(int(way.attrib["id"]))
                except (KeyError, ValueError):
                    pass
        self.road_ids_by_chunk[chunk_key] = road_ids
        self.visuals[chunk_key] = self.parse_visuals(root)
        self._rebuild_intersections_fast()

    def _drain_completed(self):
        merged = downloader_hits = 0
        for _ in range(self.max_merges_per_frame):
            try:
                key, source, error = self.completed.get_nowait()
            except queue.Empty:
                break
            self.pending_chunks.discard(key)
            self.queued_chunks.discard(key)
            started = self.request_started.pop(key, None)
            elapsed = time.monotonic() - started if started is not None else None
            if error:
                self.last_error = error
                self.requested_chunks.discard(key)
                self.status_events.appendleft((time.monotonic(), f"FAILED/retry {key[0]},{key[1]}"))
                self.retry_after[key] = time.monotonic() + 5.0
                continue
            if source == "waiting":
                # The downloader returned before the expected cache file existed.
                # Do NOT permanently mark this chunk as requested: release it and
                # retry after a short delay. This supports project downloaders that
                # enqueue work internally or occasionally decline due to rate limits.
                self.requested_chunks.discard(key)
                self.retry_after[key] = time.monotonic() + 1.25
                suffix = f" ({elapsed:.1f}s)" if elapsed is not None else ""
                self.status_events.appendleft((time.monotonic(), f"Waiting/retry {key[0]},{key[1]}{suffix}"))
                continue
            path = self.chunk_path(*key)
            if not path.exists():
                continue
            try:
                self.merge_osm_file(path, key)
                self.loaded_chunks.add(key)
                merged += 1
                downloader_hits += int(source == "downloader")
                if source == "downloader":
                    self.total_downloaded_chunks += 1
                else:
                    self.total_cache_chunks += 1
                suffix = f" ({elapsed:.1f}s)" if elapsed is not None else ""
                origin = "Downloaded" if source == "downloader" else "Loaded cache"
                self.status_events.appendleft((time.monotonic(), f"{origin} {key[0]},{key[1]}{suffix}"))
                self.last_error = ""
            except Exception as exc:
                self.last_error = f"Could not parse {path.name}: {exc}"
                self.retry_after[key] = time.monotonic() + 5.0
        if merged:
            self.revision += 1
        return merged, downloader_hits

    def ensure_view(self, camera: Camera) -> int:
        loaded, downloader_hits = self._drain_completed()
        wanted = self.chunks_for_view(camera)
        self._poll_cache(wanted)
        for key in wanted:
            if len(self.pending_chunks) >= self.max_pending:
                break
            self.request_chunk(key)
        self.generated_last_update = loaded
        self.downloads_last_update = downloader_hits

        if not self.downloader.available and not self.offline and self.downloader.error:
            self.last_error = self.downloader.error + f" | cache={self.cache_dir}"
        return loaded


# -----------------------------------------------------------------------------
# Fast per-frame simulation indexes
# -----------------------------------------------------------------------------
class VehicleFrameIndex:
    """O(V log V) nearest-car lookup instead of Vehicle's O(V^2) scan."""

    def __init__(self) -> None:
        self.ahead_distance: dict[int, float] = {}

    def rebuild(self, vehicles: list) -> None:
        groups: dict[int, list] = defaultdict(list)
        for vehicle in vehicles:
            groups[id(vehicle.segment)].append(vehicle)

        ahead: dict[int, float] = {}
        for group in groups.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda v: v.position)
            for current, nxt in zip(group, group[1:]):
                ahead[id(current)] = nxt.position - current.position
        self.ahead_distance = ahead

    def distance_ahead(self, vehicle) -> Optional[float]:
        return self.ahead_distance.get(id(vehicle))


def _fast_distance_to_vehicle_ahead(vehicle):
    index = getattr(vehicle.simulation, "_vehicle_frame_index", None)
    if index is None:
        return None
    return index.distance_ahead(vehicle)


def _fast_traffic_light_state(vehicle):
    cache = getattr(vehicle.simulation, "_signal_state_cache", None)
    if cache is None:
        return None
    return cache.get(vehicle.segment.v)


# The original methods perform linear scans through all vehicles/intersections.
# This viewer supplies equivalent per-frame lookup tables instead.
Vehicle.distance_to_vehicle_ahead = _fast_distance_to_vehicle_ahead
Vehicle.get_traffic_light_state = _fast_traffic_light_state

# -----------------------------------------------------------------------------
# Drawing helpers
# -----------------------------------------------------------------------------
def road_palette(highway: Optional[str]) -> tuple[tuple[int, int, int], float]:
    if highway == "motorway":
        return ROAD_MOTORWAY, 7.5
    if highway in {"motorway_link", "trunk", "trunk_link"}:
        return ROAD_TRUNK, 6.7
    if highway in {"primary", "primary_link"}:
        return ROAD_PRIMARY, 6.0
    if highway in {"secondary", "secondary_link"}:
        return ROAD_SECONDARY, 5.2
    if highway in {"tertiary", "tertiary_link"}:
        return ROAD_TERTIARY, 4.2
    return ROAD_LOCAL, 3.2


def polygon_visible(points: list[tuple[int, int]], width: int, height: int, margin: int = 80) -> bool:
    return any(-margin <= x <= width + margin and -margin <= y <= height + margin for x, y in points)


def draw_text_halo(surface: pygame.Surface, font: pygame.font.Font, text: str,
                   center: tuple[float, float], angle: float) -> None:
    # Keep labels upright rather than allowing upside-down road names.
    degrees = math.degrees(angle)
    if degrees > 90 or degrees < -90:
        degrees += 180
    halo = font.render(text, True, STREET_HALO)
    fg = font.render(text, True, STREET_TEXT)
    halo = pygame.transform.rotate(halo, degrees)
    fg = pygame.transform.rotate(fg, degrees)
    rect = fg.get_rect(center=(int(center[0]), int(center[1])))
    # Four small halo offsets are enough to keep text readable over roads.
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        surface.blit(halo, halo.get_rect(center=(rect.centerx + dx, rect.centery + dy)))
    surface.blit(fg, rect)


class TrafficVisualizer:
    def __init__(self, network: RoadNetwork, simulation: Simulation,
                 chunks: OSMChunkManager, initial_vehicles: int,
                 width: int = 1280, height: int = 800) -> None:
        pygame.init()
        pygame.display.set_caption("Stuttgart Traffic Simulation - shared chunk cache")
        self.screen = pygame.display.set_mode((width, height), pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("sans", 17)
        self.small_font = pygame.font.SysFont("sans", 14)
        self.label_font = pygame.font.SysFont("sans", 13)

        self.network = network
        self.simulation = simulation
        self.chunks = chunks
        self.camera = Camera(width, height)
        self.initial_vehicles = initial_vehicles
        self.initial_spawn_done = False
        self.live_traffic = TomTomTrafficManager(api_key="01wsudAvfMlglSx4dguhq9L0iciHwNM9")
        self.simulation.traffic_speed_factor = 1.0
        self._traffic_target_vehicles = initial_vehicles

        self.running = True
        self.paused = False
        self.dragging = False
        self.last_mouse = (0, 0)
        self.show_hud = True
        self.show_grid = False
        self.show_labels = True
        self.show_context = True
        self.fps = 0.0

        self.route_start_node: Optional[int] = None
        self.route_end_node: Optional[int] = None
        self.selected_route: Optional[Route] = None
        self.route_status = "Left click a road to choose route start A"

        # Interactive address/candidate-route planner.
        self.route_gui = RoutePlannerPanel(
            font=self.font,
            small_font=self.small_font,
        )
        self.address_searcher = default_stuttgart_searcher(PROJECT_ROOT)
        self.address_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="address-search",
        )
        self.address_future = None
        self.address_plan_stage = "idle"
        self.address_plan_started = 0.0
        self.address_start_match = None
        self.address_end_match = None
        self.address_required_chunks: set[tuple[int, int]] = set()
        self.address_start_xy: Optional[tuple[float, float]] = None
        self.address_end_xy: Optional[tuple[float, float]] = None
        self.candidate_routes: list[CandidateRoute] = []
        self.selected_candidate_index: Optional[int] = None
        self.candidate_count = 3

        self.ego_vehicle: Optional[EgoVehicle] = None
        self.drive_cycle_recorder: Optional[DriveCycleRecorder] = None
        self.last_drive_cycle_path: Optional[Path] = None
        self.ego_sim_time_s = 0.0
        self.vehicle_config: Optional[VehicleDynamicsConfig] = None
        self.vehicle_config_error: Optional[str] = None
        self.load_selected_vehicle_config()
        self.elevation = ElevationManager(cache_path=DEFAULT_ELEVATION_CACHE)
        self.ego_waiting_for_elevation = False
        self.current_grade_deg = 0.0

        self.vehicle_frame_index = VehicleFrameIndex()
        self.simulation._vehicle_frame_index = self.vehicle_frame_index
        self.simulation._signal_state_cache = {}
        self._visible_chunk_keys: list[tuple[int, int]] = []
        self._visible_road_ids: set[int] = set()
        self._static_surface: Optional[pygame.Surface] = None
        self._static_key = None
        self._rotated_label_cache: dict[tuple[str, int], tuple[pygame.Surface, pygame.Surface]] = {}

        # Queue nearby chunks immediately; network I/O is asynchronous.
        self.chunks.ensure_view(self.camera)
        self.refresh_visible_sets()
        self.maybe_spawn_initial()

    def maybe_spawn_initial(self) -> None:
        if not self.initial_spawn_done and self.network.segments:
            spawn_vehicles(self.network, self.simulation, self.initial_vehicles, self.camera)
            self.initial_spawn_done = True

    def visible(self, point: tuple[int, int], margin: int = 100) -> bool:
        x, y = point
        return -margin <= x <= self.camera.width + margin and -margin <= y <= self.camera.height + margin

    def handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                continue

            if event.type == pygame.VIDEORESIZE:
                self.screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)
                self.camera.resize(*event.size)
                continue

            consumed, action = self.route_gui.handle_event(
                event,
                (self.camera.width, self.camera.height),
            )

            if action is not None:
                self.handle_route_gui_action(action)

            if consumed:
                continue

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_SPACE:
                    self.paused = not self.paused
                elif event.key == pygame.K_r:
                    self.camera.center_x = self.camera.center_y = 0.0
                    self.camera.pixels_per_meter = DEFAULT_PPM
                elif event.key == pygame.K_h:
                    self.show_hud = not self.show_hud
                elif event.key == pygame.K_g:
                    self.show_grid = not self.show_grid
                elif event.key == pygame.K_l:
                    self.show_labels = not self.show_labels
                elif event.key == pygame.K_b:
                    self.show_context = not self.show_context
                elif event.key == pygame.K_v:
                    spawn_vehicles(self.network, self.simulation, 10, self.camera)
                elif event.key == pygame.K_F2:
                    self.route_gui.visible = not self.route_gui.visible
                elif (
                    pygame.K_1 <= event.key <= pygame.K_5
                    and self.candidate_routes
                ):
                    candidate_index = event.key - pygame.K_0
                    self.select_candidate_route(candidate_index)
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                    self.simulation.speed = min(32.0, self.simulation.speed * 2.0)
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    self.simulation.speed = max(0.125, self.simulation.speed * 0.5)

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    self.select_route_point(event.pos)
                elif event.button == 3:
                    self.clear_route_selection()
                elif event.button == 2:
                    self.dragging = True
                    self.last_mouse = event.pos
                elif event.button == 4:
                    self.camera.zoom_at(1.15, event.pos)
                elif event.button == 5:
                    self.camera.zoom_at(1 / 1.15, event.pos)

            elif event.type == pygame.MOUSEBUTTONUP and event.button == 2:
                self.dragging = False

            elif event.type == pygame.MOUSEMOTION and self.dragging:
                dx = event.pos[0] - self.last_mouse[0]
                dy = event.pos[1] - self.last_mouse[1]
                self.camera.pan_pixels(dx, dy)
                self.last_mouse = event.pos

            elif event.type == pygame.MOUSEWHEEL:
                self.camera.zoom_at(1.15 ** event.y, pygame.mouse.get_pos())

        # Do not pan the map while the user is typing an address.
        if self.route_gui.text_input_active:
            return

        keys = pygame.key.get_pressed()
        dt = 1.0 / max(self.clock.get_fps(), 30.0)
        speed = 650.0 / max(self.camera.pixels_per_meter, 0.01)

        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            self.camera.center_x -= speed * dt
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            self.camera.center_x += speed * dt
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            self.camera.center_y += speed * dt
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            self.camera.center_y -= speed * dt

    def handle_route_gui_action(self, action: GuiAction) -> None:
        if action.kind == "search":
            self.begin_address_search()
        elif action.kind == "select_candidate":
            self.select_candidate_route(int(action.value))
        elif action.kind == "drive_selected":
            self.drive_selected_candidate()
        elif action.kind == "clear":
            self.clear_route_selection()

    def begin_address_search(self) -> None:
        start_query = self.route_gui.start_text.strip()
        end_query = self.route_gui.end_text.strip()

        if not start_query or not end_query:
            self.route_gui.set_status(
                "Enter both a start address and a destination.",
                busy=False,
            )
            return

        if self.address_future is not None and not self.address_future.done():
            return

        self.finish_drive_cycle("address_route_replaced")
        self.drive_cycle_recorder = None
        self.ego_vehicle = None
        self.ego_waiting_for_elevation = False

        self.route_start_node = None
        self.route_end_node = None
        self.selected_route = None
        self.candidate_routes = []
        self.selected_candidate_index = None
        self.address_required_chunks.clear()
        self.route_gui.clear_candidates()

        self.address_plan_stage = "searching"
        self.address_plan_started = time.monotonic()
        self.route_gui.set_status(
            "Searching Stuttgart addresses...",
            busy=True,
        )
        self.route_status = "Address search in progress"

        def worker():
            start_results = self.address_searcher.search(
                start_query,
                limit=5,
            )
            end_results = self.address_searcher.search(
                end_query,
                limit=5,
            )
            return start_results, end_results

        self.address_future = self.address_executor.submit(worker)

    def _fit_camera_to_address_pair(
        self,
        start_xy: tuple[float, float],
        end_xy: tuple[float, float],
    ) -> None:
        x1, y1 = start_xy
        x2, y2 = end_xy

        self.camera.center_x = 0.5 * (x1 + x2)
        self.camera.center_y = 0.5 * (y1 + y2)

        dx = max(300.0, abs(x2 - x1) + 700.0)
        dy = max(300.0, abs(y2 - y1) + 700.0)

        usable_width = max(
            320,
            self.camera.width - RoutePlannerPanel.WIDTH - 60,
        )
        usable_height = max(
            280,
            self.camera.height - 80,
        )

        ppm_x = usable_width / dx
        ppm_y = usable_height / dy

        self.camera.pixels_per_meter = max(
            MIN_ZOOM,
            min(
                3.0,
                ppm_x,
                ppm_y,
            ),
        )

    def _build_address_corridor_chunks(
        self,
        start_xy: tuple[float, float],
        end_xy: tuple[float, float],
    ) -> set[tuple[int, int]]:
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
                    / (CHUNK_SIZE * 0.75)
                )
            ),
        )

        keys: set[tuple[int, int]] = set()

        for i in range(steps + 1):
            alpha = i / steps
            x = x1 + (x2 - x1) * alpha
            y = y1 + (y2 - y1) * alpha

            cx, cy = world_to_chunk(
                x,
                y,
            )

            for dx in range(
                -ADDRESS_CORRIDOR_RADIUS_CHUNKS,
                ADDRESS_CORRIDOR_RADIUS_CHUNKS + 1,
            ):
                for dy in range(
                    -ADDRESS_CORRIDOR_RADIUS_CHUNKS,
                    ADDRESS_CORRIDOR_RADIUS_CHUNKS + 1,
                ):
                    keys.add(
                        (
                            cx + dx,
                            cy + dy,
                        )
                    )

        # Give the endpoint neighborhoods a little extra room for snapping.
        for x, y in (
            start_xy,
            end_xy,
        ):
            cx, cy = world_to_chunk(
                x,
                y,
            )

            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    keys.add(
                        (
                            cx + dx,
                            cy + dy,
                        )
                    )

        return keys

    def poll_address_planner(self) -> None:
        if self.address_plan_stage == "searching":
            if self.address_future is None or not self.address_future.done():
                return

            try:
                start_results, end_results = self.address_future.result()
            except Exception as exc:
                self.address_plan_stage = "error"
                self.route_gui.set_status(
                    f"Address search failed: {exc}",
                    busy=False,
                )
                self.route_status = f"Address search failed: {exc}"
                return

            if not start_results:
                self.address_plan_stage = "error"
                self.route_gui.set_status(
                    "No Stuttgart match found for the start address.",
                    busy=False,
                )
                return

            if not end_results:
                self.address_plan_stage = "error"
                self.route_gui.set_status(
                    "No Stuttgart match found for the destination.",
                    busy=False,
                )
                return

            self.address_start_match = start_results[0]
            self.address_end_match = end_results[0]

            self.route_gui.set_matches(
                self.address_start_match.display_name,
                self.address_end_match.display_name,
            )

            self.address_start_xy = latlon_to_local(
                self.address_start_match.latitude,
                self.address_start_match.longitude,
            )

            self.address_end_xy = latlon_to_local(
                self.address_end_match.latitude,
                self.address_end_match.longitude,
            )

            self._fit_camera_to_address_pair(
                self.address_start_xy,
                self.address_end_xy,
            )

            self.address_required_chunks = self._build_address_corridor_chunks(
                self.address_start_xy,
                self.address_end_xy,
            )

            self.address_plan_stage = "loading"
            self.address_plan_started = time.monotonic()

        if self.address_plan_stage != "loading":
            return

        for key in sorted(
            self.address_required_chunks,
            key=lambda item: (
                abs(
                    item[0]
                    - world_to_chunk(
                        self.camera.center_x,
                        self.camera.center_y,
                    )[0]
                )
                + abs(
                    item[1]
                    - world_to_chunk(
                        self.camera.center_x,
                        self.camera.center_y,
                    )[1]
                )
            ),
        ):
            if len(self.chunks.pending_chunks) >= self.chunks.max_pending:
                break
            self.chunks.request_chunk(key)

        loaded = len(
            self.address_required_chunks
            & self.chunks.loaded_chunks
        )
        total = len(
            self.address_required_chunks
        )

        self.route_gui.set_status(
            f"Loading road corridor: {loaded}/{total} chunks",
            busy=True,
        )

        elapsed = (
            time.monotonic()
            - self.address_plan_started
        )

        ready_enough = (
            total > 0
            and loaded == total
        )

        timed_out = (
            elapsed >= ADDRESS_PLAN_TIMEOUT_S
        )

        if not ready_enough and not timed_out:
            return

        try:
            start_node, start_snap = nearest_drivable_node_latlon(
                self.network,
                self.address_start_match.latitude,
                self.address_start_match.longitude,
                maximum_distance_m=1500.0,
            )

            end_node, end_snap = nearest_drivable_node_latlon(
                self.network,
                self.address_end_match.latitude,
                self.address_end_match.longitude,
                maximum_distance_m=1500.0,
            )

            candidates = plan_candidate_routes(
                self.network,
                start_node,
                end_node,
                count=self.candidate_count,
                cost_mode="time",
                diversity_penalty=0.75,
                max_overlap=0.92,
                max_attempts=30,
            )
        except Exception as exc:
            self.address_plan_stage = "error"
            self.route_gui.set_status(
                f"Could not plan address route: {exc}",
                busy=False,
            )
            self.route_status = f"Address route failed: {exc}"
            return

        if not candidates:
            self.address_plan_stage = "error"
            self.route_gui.set_status(
                "No directed route found. Try closer addresses or load more map area.",
                busy=False,
            )
            self.route_status = "No directed address route found"
            return

        self.route_start_node = start_node
        self.route_end_node = end_node
        self.candidate_routes = list(candidates)
        self.selected_candidate_index = 1
        self.selected_route = candidates[0].route

        gui_candidates = [
            {
                "index": candidate.candidate_index,
                "distance_km": candidate.route.distance_m / 1000.0,
                "time_min": candidate.route.estimated_time_s / 60.0,
                "overlap": candidate.overlap_with_best,
            }
            for candidate in candidates
        ]

        self.route_gui.set_candidates(
            gui_candidates
        )
        self.route_gui.select_candidate(
            1
        )
        self.route_gui.set_status(
            f"{len(candidates)} candidate routes ready. Select one, then Drive selected.",
            busy=False,
        )

        self.route_status = (
            f"{len(candidates)} address-route candidates ready; "
            f"start snap {start_snap:.0f} m, end snap {end_snap:.0f} m"
        )
        self.address_plan_stage = "ready"

    def select_candidate_route(
        self,
        candidate_index: int,
    ) -> None:
        candidate = next(
            (
                item
                for item in self.candidate_routes
                if item.candidate_index == candidate_index
            ),
            None,
        )

        if candidate is None:
            return

        if self.ego_vehicle is not None:
            self.finish_drive_cycle("candidate_switched")

        self.ego_vehicle = None
        self.drive_cycle_recorder = None
        self.ego_waiting_for_elevation = False

        self.selected_candidate_index = candidate_index
        self.selected_route = candidate.route
        self.route_gui.select_candidate(
            candidate_index
        )

        self.route_status = (
            f"Candidate {candidate_index}: "
            f"{candidate.route.distance_m / 1000.0:.2f} km, "
            f"{candidate.route.estimated_time_s / 60.0:.1f} min - "
            "press Drive selected"
        )

    def drive_selected_candidate(self) -> None:
        if self.selected_route is None:
            self.route_gui.set_status(
                "Select a candidate route first.",
                busy=False,
            )
            return

        self.route_gui.set_status(
            f"Preparing Route {self.selected_candidate_index} elevation...",
            busy=True,
        )

        self.prepare_route_elevation()

    def load_selected_vehicle_config(self) -> None:
        try:
            self.vehicle_config = load_vehicle_config(DEFAULT_CAR_CONFIG)
            self.vehicle_config_error = None
            print(
                f"[drive-cycle] vehicle config loaded: "
                f"{self.vehicle_config.name} ({DEFAULT_CAR_CONFIG})"
            )
        except Exception as exc:
            self.vehicle_config = None
            self.vehicle_config_error = str(exc)
            print(f"[drive-cycle] vehicle config error: {exc}")

    def clear_route_selection(self) -> None:
        self.finish_drive_cycle("route_cleared")

        self.route_start_node = None
        self.route_end_node = None
        self.selected_route = None
        self.candidate_routes = []
        self.selected_candidate_index = None
        self.address_required_chunks.clear()
        self.address_plan_stage = "idle"
        self.ego_vehicle = None
        self.drive_cycle_recorder = None
        self.ego_waiting_for_elevation = False
        self.current_grade_deg = 0.0
        self.route_gui.clear_candidates()
        self.route_gui.set_status(
            "Type Stuttgart addresses, or click A/B directly on the map.",
            busy=False,
        )
        self.route_status = "Left click a road to choose route start A"

    def select_route_point(self, screen_pos: tuple[int, int]) -> None:
        world_x, world_y = self.camera.screen_to_world(*screen_pos)
        tolerance_m = max(8.0, min(45.0, 22.0 / max(self.camera.pixels_per_meter, 0.01)))

        node_id = nearest_road_node(
            self.network,
            world_x,
            world_y,
            max_distance_m=tolerance_m,
        )

        if node_id is None:
            self.route_status = "No drivable road node close to that click"
            return

        if self.route_start_node is None or self.route_end_node is not None:
            self.finish_drive_cycle("route_replaced")
            self.drive_cycle_recorder = None
            self.candidate_routes = []
            self.selected_candidate_index = None
            self.route_gui.clear_candidates()
            self.address_plan_stage = "idle"
            self.address_required_chunks.clear()

            self.route_start_node = node_id
            self.route_end_node = None
            self.selected_route = None
            self.ego_vehicle = None
            self.ego_waiting_for_elevation = False
            node = self.network.nodes[node_id]
            self.route_status = (
                f"A selected at ({node.x:.0f}, {node.y:.0f}) m - left click destination B"
            )
            return

        if node_id == self.route_start_node:
            self.route_status = "Choose a different road node for destination B"
            return

        self.route_end_node = node_id
        route = plan_route(
            self.network,
            self.route_start_node,
            self.route_end_node,
            cost_mode="time",
        )

        if route is None:
            self.selected_route = None
            self.route_status = (
                "No directed route found in loaded road chunks - try closer points or load the corridor"
            )
            return

        self.selected_route = route
        self.prepare_route_elevation()

        self.route_status = (
            f"Route {route.distance_m / 1000.0:.2f} km selected - "
            "loading elevation before ego starts"
        )

    def prepare_route_elevation(self) -> None:
        route = self.selected_route

        if route is None:
            self.ego_waiting_for_elevation = False
            return

        self.elevation.request_nodes(
            self.network,
            route.node_ids,
        )

        if self.elevation.route_ready(
            route.node_ids
        ):
            self.ego_waiting_for_elevation = False
            self.route_gui.set_status(
                f"Driving Route {self.selected_candidate_index or 1}...",
                busy=False,
            )
            self.spawn_ego_vehicle()
            return

        self.ego_waiting_for_elevation = True

        ready, total = self.elevation.route_progress(
            route.node_ids
        )

        self.route_status = (
            f"Loading route elevation: "
            f"{ready}/{total} nodes ready"
        )

    def ego_grade_deg(self) -> float:
        vehicle = self.ego_vehicle

        if (
            vehicle is None
            or vehicle.segment is None
        ):
            return 0.0

        grade = self.elevation.segment_grade_deg(
            vehicle.segment,
            self.network,
        )

        if grade is None:
            return 0.0

        return grade

    def spawn_ego_vehicle(self) -> None:
        route = self.selected_route

        if self.vehicle_config is None:
            self.ego_vehicle = None
            self.drive_cycle_recorder = None
            self.route_status = "Cannot spawn ego: vehicle YAML failed to load"
            return

        if route is None or not route.segments:
            self.ego_vehicle = None
            self.drive_cycle_recorder = None
            return

        self.finish_drive_cycle("route_replaced")

        next_id = max(
            (v.id for v in self.simulation.vehicles),
            default=-1,
        ) + 100000

        self.ego_vehicle = EgoVehicle(
            vehicle_id=next_id,
            route_segments=route.segments,
            network=self.network,
            simulation=self.simulation,
            config=self.vehicle_config,
        )

        self.ego_sim_time_s = 0.0

        self.drive_cycle_recorder = DriveCycleRecorder(
            vehicle_id=next_id,
            output_dir=DEFAULT_DRIVE_CYCLE_DIR,
            route_node_count=len(route.node_ids),
            route_distance_m=route.distance_m,
            sample_interval_s=0.05,
        )

        self.current_grade_deg = self.ego_grade_deg()

        self.drive_cycle_recorder.start(
            self.ego_sim_time_s,
            initial_speed_mps=self.ego_vehicle.speed,
            grade_deg=self.current_grade_deg,
        )

        self.route_status = (
            f"Ego recording started - "
            f"{route.distance_m / 1000.0:.2f} km route"
        )

    def finish_drive_cycle(self, status: str) -> None:
        recorder = self.drive_cycle_recorder

        if recorder is None or recorder.saved:
            return

        path = recorder.save(
            status=status,
            extra_metadata={
                "traffic_speed_factor": getattr(
                    self.simulation,
                    "traffic_speed_factor",
                    1.0,
                ),
                "vehicle_config": (
                    self.vehicle_config.name
                    if self.vehicle_config is not None
                    else "unknown"
                ),
                "elevation_dataset": self.elevation.dataset,
                "elevation_cache": str(DEFAULT_ELEVATION_CACHE),
            },
        )

        if path is not None:
            self.last_drive_cycle_path = path

    def draw_ego_vehicle(self) -> None:
        vehicle = self.ego_vehicle

        if vehicle is None or vehicle.segment is None:
            return

        x, y = vehicle.get_position()
        center = self.camera.world_to_screen(x, y)

        if not self.visible(center, 40):
            return

        a = self.network.nodes.get(vehicle.segment.u)
        b = self.network.nodes.get(vehicle.segment.v)

        if a is None or b is None:
            return

        angle = math.atan2(
            -(b.y - a.y),
            b.x - a.x,
        )

        ppm = self.camera.pixels_per_meter
        length = max(10.0, min(22.0, 5.0 * ppm))
        width = max(5.0, min(10.0, 2.1 * ppm))

        forward = pygame.Vector2(
            math.cos(angle),
            math.sin(angle),
        )
        right = pygame.Vector2(
            -forward.y,
            forward.x,
        )
        c = pygame.Vector2(center)

        corners = [
            c + forward * length / 2 + right * width / 2,
            c + forward * length / 2 - right * width / 2,
            c - forward * length / 2 - right * width / 2,
            c - forward * length / 2 + right * width / 2,
        ]

        pygame.draw.polygon(
            self.screen,
            EGO_VEHICLE,
            corners,
        )
        pygame.draw.polygon(
            self.screen,
            EGO_VEHICLE_EDGE,
            corners,
            2,
        )

        label = self.small_font.render(
            "EGO",
            True,
            EGO_VEHICLE_EDGE,
        )
        self.screen.blit(
            label,
            (
                center[0] - label.get_width() // 2,
                center[1] - 24,
            ),
        )

    def draw_candidate_routes(self) -> None:
        if not self.candidate_routes:
            return

        for candidate in self.candidate_routes:
            route = candidate.route

            if len(route.node_ids) < 2:
                continue

            points = []

            for node_id in route.node_ids:
                node = self.network.nodes.get(
                    node_id
                )

                if node is not None:
                    points.append(
                        self.camera.world_to_screen(
                            node.x,
                            node.y,
                        )
                    )

            if len(points) < 2:
                continue

            selected = (
                candidate.candidate_index
                == self.selected_candidate_index
            )

            color = (
                ROUTE_CANDIDATE_COLORS[
                    (candidate.candidate_index - 1)
                    % len(ROUTE_CANDIDATE_COLORS)
                ]
                if selected
                else ROUTE_CANDIDATE_MUTED
            )

            width = (
                max(
                    4,
                    int(
                        4
                        * min(
                            self.camera.pixels_per_meter,
                            2.0,
                        )
                    ),
                )
                if selected
                else 2
            )

            pygame.draw.lines(
                self.screen,
                (248, 248, 248),
                False,
                points,
                width + 2,
            )

            pygame.draw.lines(
                self.screen,
                color,
                False,
                points,
                width,
            )

    def draw_selected_route(self) -> None:
        route = self.selected_route
        if route is not None and len(route.node_ids) >= 2:
            points = []
            for node_id in route.node_ids:
                node = self.network.nodes.get(node_id)
                if node is not None:
                    points.append(self.camera.world_to_screen(node.x, node.y))

            if len(points) >= 2:
                pygame.draw.lines(
                    self.screen, ROUTE_MARKER_EDGE, False, points,
                    max(5, int(5 * min(self.camera.pixels_per_meter, 2.0))),
                )
                pygame.draw.lines(
                    self.screen, ROUTE_LINE, False, points,
                    max(3, int(3 * min(self.camera.pixels_per_meter, 2.0))),
                )

        self.draw_route_marker(self.route_start_node, ROUTE_START, "A")
        self.draw_route_marker(self.route_end_node, ROUTE_END, "B")

    def draw_route_marker(self, node_id: Optional[int], color, label: str) -> None:
        if node_id is None:
            return
        node = self.network.nodes.get(node_id)
        if node is None:
            return
        point = self.camera.world_to_screen(node.x, node.y)
        if not self.visible(point, 40):
            return

        radius = 9
        pygame.draw.circle(self.screen, ROUTE_MARKER_EDGE, point, radius + 2)
        pygame.draw.circle(self.screen, color, point, radius)
        text = self.small_font.render(label, True, (255, 255, 255))
        self.screen.blit(text, text.get_rect(center=point))

    def refresh_visible_sets(self) -> None:
        self._visible_chunk_keys = self.chunks.chunks_for_view(self.camera)
        ids: set[int] = set()
        for key in self._visible_chunk_keys:
            ids.update(self.chunks.road_ids_by_chunk.get(key, ()))
        self._visible_road_ids = ids

    def refresh_signal_cache(self) -> None:
        cache = {}
        for intersection in self.network.intersections:
            state = intersection.state
            if state == "A_GREEN":
                a, b = "green", "red"
            elif state == "A_YELLOW":
                a, b = "yellow", "red"
            elif state == "B_GREEN":
                a, b = "red", "green"
            else:
                a, b = "red", "yellow"
            for node_id in intersection.phase_a:
                cache[node_id] = a
            for node_id in intersection.phase_b:
                cache[node_id] = b
        self.simulation._signal_state_cache = cache

    def static_map_key(self):
        # Exact camera state gives perfect visuals while still making the common
        # stationary-camera case essentially free after the first frame.
        return (
            self.camera.width, self.camera.height,
            round(self.camera.center_x, 4), round(self.camera.center_y, 4),
            round(self.camera.pixels_per_meter, 5), self.chunks.revision,
            self.show_context, self.show_labels, self.show_grid,
        )

    def get_static_map(self) -> pygame.Surface:
        key = self.static_map_key()
        if self._static_surface is not None and key == self._static_key:
            return self._static_surface
        surface = pygame.Surface((self.camera.width, self.camera.height)).convert()
        surface.fill(BACKGROUND)
        self.draw_context(surface)
        self.draw_roads(surface)
        self.draw_street_labels(surface)
        self.draw_grid(surface)
        self._static_surface = surface
        self._static_key = key
        return surface

    def draw_context(self, surface: Optional[pygame.Surface] = None) -> None:
        if not self.show_context:
            return
        surface = surface or self.screen
        for key in self._visible_chunk_keys:
            visuals = self.chunks.visuals.get(key)
            if visuals is None:
                continue
            for poly in visuals.polygons:
                pts = [self.camera.world_to_screen(x, y) for x, y in poly.points]
                if len(pts) < 3 or not polygon_visible(pts, self.camera.width, self.camera.height):
                    continue
                color = BUILDING if poly.kind == "building" else PARK if poly.kind == "park" else WATER
                pygame.draw.polygon(surface, color, pts)
                if poly.kind == "building" and self.camera.pixels_per_meter >= 1.0:
                    pygame.draw.lines(surface, BUILDING_EDGE, True, pts, 1)
            for line in visuals.lines:
                pts = [self.camera.world_to_screen(x, y) for x, y in line.points]
                if len(pts) < 2 or not polygon_visible(pts, self.camera.width, self.camera.height):
                    continue
                pygame.draw.lines(surface, WATER if line.kind == "water" else RAIL,
                                  False, pts, 2 if line.kind == "water" else 1)

    def draw_roads(self, surface: Optional[pygame.Surface] = None) -> None:
        surface = surface or self.screen
        ppm = self.camera.pixels_per_meter
        for road_id in self._visible_road_ids:
            road = self.network.roads.get(road_id)
            if road is None:
                continue
            if road.highway not in DRIVABLE or len(road.nodes) < 2:
                continue
            pts = []
            for node_id in road.nodes:
                node = self.network.nodes.get(node_id)
                if node is not None:
                    pts.append(self.camera.world_to_screen(node.x, node.y))
            if len(pts) < 2 or not polygon_visible(pts, self.camera.width, self.camera.height):
                continue

            fill, meters = road_palette(road.highway)
            width = max(2, min(18, int(meters * ppm)))
            casing = width + max(1, int(ppm * 0.8))
            pygame.draw.lines(surface, ROAD_CASING, False, pts, casing)
            pygame.draw.lines(surface, fill, False, pts, width)
            if width >= 10 and road.highway in {"motorway", "trunk", "primary"}:
                pygame.draw.lines(surface, ROAD_CENTER, False, pts, 1)

    def draw_street_labels(self, surface: Optional[pygame.Surface] = None) -> None:
        if not self.show_labels or self.camera.pixels_per_meter < 0.75:
            return
        surface = surface or self.screen

        # Avoid drawing the same OSM street name repeatedly on every constituent way.
        drawn: list[pygame.Rect] = []
        seen_names: set[str] = set()
        candidates = []
        for road_id in self._visible_road_ids:
            road = self.network.roads.get(road_id)
            if road is None or not road.name or road.highway not in DRIVABLE or len(road.nodes) < 2:
                continue
            # Prefer important roads when labels compete for space.
            priority = {
                "motorway": 0, "trunk": 1, "primary": 2, "secondary": 3,
                "tertiary": 4, "residential": 5,
            }.get(road.highway, 6)
            candidates.append((priority, road))
        candidates.sort(key=lambda item: item[0])

        for _, road in candidates:
            name = road.name.strip()
            if not name or name in seen_names:
                continue
            nodes = [self.network.nodes.get(n) for n in road.nodes]
            nodes = [n for n in nodes if n is not None]
            if len(nodes) < 2:
                continue

            # Pick the longest visible segment as the label anchor.
            best = None
            best_len = 0.0
            for a, b in zip(nodes, nodes[1:]):
                p1 = self.camera.world_to_screen(a.x, a.y)
                p2 = self.camera.world_to_screen(b.x, b.y)
                if not (self.visible(p1, 50) or self.visible(p2, 50)):
                    continue
                length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                if length > best_len:
                    best_len = length
                    best = (p1, p2)
            if best is None or best_len < max(45, len(name) * 6):
                continue

            p1, p2 = best
            mid = ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)
            angle = math.atan2(-(p2[1] - p1[1]), p2[0] - p1[0])
            test = self.label_font.render(name, True, STREET_TEXT)
            rect = test.get_rect(center=(int(mid[0]), int(mid[1]))).inflate(8, 5)
            if any(rect.colliderect(other) for other in drawn):
                continue
            self.draw_cached_label(surface, name, mid, angle)
            drawn.append(rect)
            seen_names.add(name)

    def draw_cached_label(self, surface: pygame.Surface, text: str,
                          center: tuple[float, float], angle: float) -> None:
        degrees = math.degrees(angle)
        if degrees > 90 or degrees < -90:
            degrees += 180
        bucket = int(round(degrees / 2.0) * 2)
        key = (text, bucket)
        pair = self._rotated_label_cache.get(key)
        if pair is None:
            halo = pygame.transform.rotate(self.label_font.render(text, True, STREET_HALO), bucket)
            fg = pygame.transform.rotate(self.label_font.render(text, True, STREET_TEXT), bucket)
            pair = (halo, fg)
            if len(self._rotated_label_cache) > 1500:
                self._rotated_label_cache.clear()
            self._rotated_label_cache[key] = pair
        halo, fg = pair
        rect = fg.get_rect(center=(int(center[0]), int(center[1])))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            surface.blit(halo, halo.get_rect(center=(rect.centerx + dx, rect.centery + dy)))
        surface.blit(fg, rect)

    def signal_state(self, node_id: int) -> Optional[str]:
        return self.simulation._signal_state_cache.get(node_id)

    def draw_lights(self) -> None:
        radius = max(3, min(7, int(2 + self.camera.pixels_per_meter)))
        for node_id in self.network.traffic_lights:
            node = self.network.nodes.get(node_id)
            if node is None:
                continue
            p = self.camera.world_to_screen(node.x, node.y)
            if not self.visible(p, 20):
                continue
            color = {"green": GREEN, "yellow": YELLOW, "red": RED}.get(
                self.signal_state(node_id), UNKNOWN_LIGHT)
            pygame.draw.circle(self.screen, (45, 45, 45), p, radius + 2)
            pygame.draw.circle(self.screen, color, p, radius)

    def draw_vehicles(self) -> None:
        for vehicle in self.simulation.vehicles:
            try:
                x, y = vehicle.get_position()
            except Exception:
                continue
            center = self.camera.world_to_screen(x, y)
            if not self.visible(center, 30):
                continue
            a = self.network.nodes.get(vehicle.segment.u)
            b = self.network.nodes.get(vehicle.segment.v)
            if a is None or b is None:
                continue
            angle = math.atan2(-(b.y - a.y), b.x - a.x)
            ppm = self.camera.pixels_per_meter
            if ppm < 0.7:
                pygame.draw.circle(self.screen, VEHICLE, center, 2)
                continue
            length = max(7.0, min(17.0, 4.5 * ppm))
            width = max(4.0, min(8.0, 1.9 * ppm))
            f = pygame.Vector2(math.cos(angle), math.sin(angle))
            r = pygame.Vector2(-f.y, f.x)
            c = pygame.Vector2(center)
            corners = [
                c + f * length/2 + r * width/2,
                c + f * length/2 - r * width/2,
                c - f * length/2 - r * width/2,
                c - f * length/2 + r * width/2,
            ]
            pygame.draw.polygon(self.screen, VEHICLE, corners)
            pygame.draw.polygon(self.screen, VEHICLE_EDGE, corners, 1)

    def draw_grid(self, surface: Optional[pygame.Surface] = None) -> None:
        if not self.show_grid:
            return
        surface = surface or self.screen
        for cx, cy in self._visible_chunk_keys:
            min_x, min_y, max_x, max_y = chunk_bounds(cx, cy)
            a = self.camera.world_to_screen(min_x, min_y)
            b = self.camera.world_to_screen(max_x, max_y)
            rect = pygame.Rect(min(a[0], b[0]), min(a[1], b[1]), abs(b[0]-a[0]), abs(b[1]-a[1]))
            pygame.draw.rect(surface, GRID, rect, 1)

    def draw_stream_status(self) -> None:
        """Bottom-corner status panels for cache/download activity."""
        now = time.monotonic()
        pad = 12
        margin = 14
        line_gap = 3

        # -----------------------------
        # Bottom-left: current activity
        # -----------------------------
        active = sorted(
            self.chunks.pending_chunks,
            key=lambda k: self.chunks.request_started.get(k, now),
        )
        spinner = "|/-\\"[int(now * 8) % 4]
        adapter = self.chunks.downloader.name if self.chunks.downloader.available else "NO DOWNLOADER FOUND"

        left_lines = [
            ("CHUNK STREAM", HUD_TEXT),
            (f"Source  {adapter}", HUD_MUTED),
            (f"{spinner} Active {len(active):2d}   Parse queue {len(self.chunks.queued_chunks):2d}", HUD_TEXT),
        ]

        if active:
            for key in active[:4]:
                started = self.chunks.request_started.get(key, now)
                elapsed = max(0.0, now - started)
                path = self.chunks.chunk_path(*key)
                size_text = ""
                try:
                    if path.exists():
                        size = path.stat().st_size
                        size_text = f"  {size / 1024:.0f} KB"
                except OSError:
                    pass
                left_lines.append((f"  {key[0]:+d},{key[1]:+d}  {elapsed:5.1f}s{size_text}", HUD_MUTED))
            if len(active) > 4:
                left_lines.append((f"  +{len(active) - 4} more", HUD_MUTED))
        elif self.chunks.offline:
            left_lines.append(("  Offline - cache only", HUD_MUTED))
        elif self.chunks.downloader.available:
            left_lines.append(("  Idle - waiting for next retry/cache file", HUD_MUTED))
        else:
            left_lines.append(("  Waiting for shared cache files", HUD_MUTED))

        left_lines.append((
            f"Requests {self.chunks.total_download_requests}  downloaded {self.chunks.total_downloaded_chunks}",
            HUD_MUTED,
        ))

        # -----------------------------
        # Bottom-right: recent results
        # -----------------------------
        right_lines = [("RECENT CHUNKS", HUD_TEXT)]
        recent = list(self.chunks.status_events)[:5]
        if recent:
            for when, text in recent:
                age = max(0.0, now - when)
                right_lines.append((f"{text}  {age:4.0f}s ago", HUD_MUTED))
        else:
            right_lines.append(("No chunk activity yet", HUD_MUTED))

        if self.chunks.last_error:
            err = self.chunks.last_error.replace("\n", " ")
            if len(err) > 58:
                err = err[:55] + "..."
            right_lines.append((f"ERROR: {err}", (245, 150, 140)))

        def render_panel(lines):
            rendered = [self.small_font.render(text, True, color) for text, color in lines]
            width = max((surf.get_width() for surf in rendered), default=0) + pad * 2
            height = sum(surf.get_height() + line_gap for surf in rendered) + pad * 2 - line_gap
            panel = pygame.Surface((width, height), pygame.SRCALPHA)
            panel.fill(HUD_BG)
            y = pad
            for surf in rendered:
                panel.blit(surf, (pad, y))
                y += surf.get_height() + line_gap
            return panel

        left_panel = render_panel(left_lines)
        right_panel = render_panel(right_lines)
        bottom_y_left = self.camera.height - left_panel.get_height() - margin
        bottom_y_right = self.camera.height - right_panel.get_height() - margin
        self.screen.blit(left_panel, (margin, bottom_y_left))
        self.screen.blit(right_panel, (self.camera.width - right_panel.get_width() - margin, bottom_y_right))

    def draw_hud(self) -> None:
        if not self.show_hud:
            return
        chunk = world_to_chunk(self.camera.center_x, self.camera.center_y)
        avg = (sum(v.speed for v in self.simulation.vehicles) / len(self.simulation.vehicles)
               if self.simulation.vehicles else 0.0)
        lines = [
            "STUTTGART / OPENSTREETMAP",
            f"FPS          {self.fps:5.1f}",
            f"Chunk        {chunk[0]:4d}, {chunk[1]:4d}",
            f"Cached/load  {len(self.chunks.loaded_chunks):7d} chunks",
            f"Road ways    {len(self.network.roads):7d}",
            f"Vehicles     {len(self.simulation.vehicles):7d}",
            f"Avg speed    {avg * 3.6:7.1f} km/h",
            f"Sim speed    {self.simulation.speed:7.3g}x",
            f"Traffic x    {getattr(self.simulation, 'traffic_speed_factor', 1.0):7.2f}",
            "",
            "WASD pan   middle-drag pan   wheel zoom",
            "Address GUI top-right   F2 toggle panel",
            "Left click A/B   right click clear route",
            "L labels   B buildings   G grid",
            "V +10 cars   R Stuttgart origin",
            "",
            self.route_status,
        ]

        if self.ego_vehicle is not None:
            ego = self.ego_vehicle
            lines += [
                f"Ego vehicle   {ego.vehicle_name}",
                f"Ego speed     {ego.speed * 3.6:7.1f} km/h",
                f"Ego accel     {ego.acceleration_mps2:7.2f} m/s2",
                f"Road grade    {self.current_grade_deg:+7.2f} deg",
                f"Ego progress  {ego.progress * 100.0:7.1f}%",
            ]

            if ego.last_dynamics is not None:
                dyn = ego.last_dynamics
                lines += [
                    f"Wheel power   {dyn.actual_wheel_power_w / 1000.0:7.1f} kW",
                    f"DC power      {dyn.dc_power_w / 1000.0:7.1f} kW",
                ]

                if dyn.propulsion_power_limited:
                    lines += ["Power limit   PROPULSION"]
                elif dyn.regen_power_limited:
                    lines += ["Power limit   REGEN/friction"]

            if self.drive_cycle_recorder is not None:
                recorder = self.drive_cycle_recorder
                state = "saved" if recorder.saved else "recording"
                lines += [
                    f"Drive cycle   {state}",
                    f"Samples       {recorder.sample_count:7d}",
                ]
        if self.vehicle_config_error:
            lines += [
                "",
                "Vehicle config error:",
                self.vehicle_config_error[:72],
            ]

        if self.elevation.last_error:
            lines += [
                "",
                self.elevation.last_error[:72],
            ]

        if self.chunks.last_error:
            lines += ["", self.chunks.last_error[:72]]
        rendered = [self.small_font.render(t, True, HUD_TEXT if i == 0 else HUD_MUTED)
                    for i, t in enumerate(lines)]
        w = max((s.get_width() for s in rendered), default=0) + 28
        h = sum(s.get_height() + 3 for s in rendered) + 18
        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        panel.fill(HUD_BG)
        self.screen.blit(panel, (14, 14))
        y = 23
        for surf in rendered:
            self.screen.blit(surf, (28, y))
            y += surf.get_height() + 3
        if self.paused:
            paused = self.font.render("PAUSED", True, (180, 80, 50))
            self.screen.blit(paused, paused.get_rect(center=(self.camera.width // 2, 28)))

    def run(self) -> None:
        while self.running:
            dt = min(self.clock.tick(60) / 1000.0, 0.1)
            self.fps = self.clock.get_fps()
            self.handle_events()

            loaded = self.chunks.ensure_view(self.camera)
            self.refresh_visible_sets()
            self.maybe_spawn_initial()

            self.elevation.poll()
            self.poll_address_planner()

            if (
                self.ego_waiting_for_elevation
                and self.selected_route is not None
            ):
                route = self.selected_route

                if self.elevation.route_ready(
                    route.node_ids
                ):
                    self.ego_waiting_for_elevation = False
                    self.route_gui.set_status(
                        f"Driving Route {self.selected_candidate_index or 1}...",
                        busy=False,
                    )
                    self.spawn_ego_vehicle()
                else:
                    ready, total = self.elevation.route_progress(
                        route.node_ids
                    )
                    self.route_status = (
                        f"Loading route elevation: "
                        f"{ready}/{total} nodes ready"
                    )

            traffic = self.live_traffic.update(self.camera)
            if traffic is not None:
                self.simulation.traffic_speed_factor = traffic.speed_factor
                self._traffic_target_vehicles = max(5, int(self.initial_vehicles * traffic.density_factor))
                deficit = self._traffic_target_vehicles - len(self.simulation.vehicles)
                if deficit > 0:
                    spawn_vehicles(self.network, self.simulation, min(deficit, 2), self.camera)
                elif deficit < -8:
                    # Remove slowly so a refresh never causes a visible traffic pop.
                    del self.simulation.vehicles[:min(2, -deficit)]
            if loaded and len(self.simulation.vehicles) < 140:
                spawn_vehicles(self.network, self.simulation, min(loaded * 2, 8), self.camera)

            if not self.paused:
                # Build compact indexes once, then every vehicle gets O(1) lookups
                # for the nearest car ahead and the upcoming traffic light.
                self.vehicle_frame_index.rebuild(self.simulation.vehicles)
                self.refresh_signal_cache()
                self.simulation.update(dt)

                if self.ego_vehicle is not None:
                    ego_dt = dt * self.simulation.speed

                    self.current_grade_deg = self.ego_grade_deg()

                    self.ego_vehicle.update(
                        ego_dt,
                        grade_deg=self.current_grade_deg,
                    )

                    self.ego_sim_time_s += ego_dt

                    # Refresh after movement in case the ego crossed onto
                    # another road segment during this step.
                    self.current_grade_deg = self.ego_grade_deg()

                    if self.drive_cycle_recorder is not None:
                        self.drive_cycle_recorder.record(
                            simulation_time_s=self.ego_sim_time_s,
                            speed_mps=self.ego_vehicle.speed,
                            grade_deg=self.current_grade_deg,
                        )

                    if self.ego_vehicle.arrived:
                        self.finish_drive_cycle("arrived")

                        if self.last_drive_cycle_path is not None:
                            self.route_status = (
                                "Ego arrived - saved "
                                f"{self.last_drive_cycle_path.name}"
                            )
                            self.route_gui.set_status(
                                f"Route finished: {self.last_drive_cycle_path.name}",
                                busy=False,
                            )
                        else:
                            self.route_status = (
                                "Ego vehicle arrived at destination B"
                            )
            else:
                self.refresh_signal_cache()

            self.screen.blit(self.get_static_map(), (0, 0))
            self.draw_candidate_routes()
            self.draw_selected_route()
            self.draw_lights()
            self.draw_vehicles()
            self.draw_ego_vehicle()
            self.draw_stream_status()
            self.draw_hud()
            self.route_gui.draw(self.screen)
            pygame.display.flip()

        self.finish_drive_cycle("application_shutdown")
        self.address_executor.shutdown(wait=False, cancel_futures=True)
        self.elevation.close()
        self.live_traffic.close()
        self.chunks.close()
        pygame.quit()


# -----------------------------------------------------------------------------
# Vehicles
# -----------------------------------------------------------------------------
def valid_spawn_segments(network: RoadNetwork, camera: Optional[Camera] = None) -> list:
    out = []
    for segment in network.segments:
        if segment.length <= 2 or segment.u not in network.nodes or segment.v not in network.nodes:
            continue
        if camera is not None:
            a = network.nodes[segment.u]
            b = network.nodes[segment.v]
            p = camera.world_to_screen((a.x+b.x)/2, (a.y+b.y)/2)
            if not (-220 <= p[0] <= camera.width+220 and -220 <= p[1] <= camera.height+220):
                continue
        out.append(segment)
    return out


def spawn_vehicles(network: RoadNetwork, simulation: Simulation, count: int,
                   camera: Optional[Camera] = None) -> None:
    candidates = valid_spawn_segments(network, camera) or valid_spawn_segments(network)
    if not candidates:
        return
    next_id = max((v.id for v in simulation.vehicles), default=-1) + 1
    for i in range(max(0, count)):
        segment = random.choice(candidates)
        v = Vehicle(next_id+i, segment, network, simulation)
        v.position = random.uniform(0.0, max(0.0, segment.length-0.1))
        v.speed = random.uniform(0.0, min(segment.speed_limit or 10.0, 8.0))
        simulation.add_vehicle(v)


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stuttgart Pygame viewer with automatic project chunk-downloader discovery")
    p.add_argument("--vehicles", type=int, default=40)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--offline", action="store_true", help="use only already-cached .osm chunks")
    p.add_argument("--downloader", default=None, metavar="MODULE:FUNCTION",
                   help="existing chunk downloader hook, e.g. chunks.downloader:ensure_chunk")
    p.add_argument("--cache", type=Path, default=None,
                   help="chunk cache directory (default: project cache/osm_chunks/stuttgart)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(7)

    # Use the exact same project root as the imported RoadNetwork module.
    # This mirrors RoadNetwork.load_area() even when this viewer is copied
    # somewhere else on disk.
    cache = (args.cache.expanduser().resolve() if args.cache else DEFAULT_CHUNK_CACHE)

    network = RoadNetwork()
    simulation = Simulation(network)
    chunks = OSMChunkManager(network, simulation, cache, offline=args.offline,
                             downloader_spec=args.downloader)

    print(f"[map] project root: {PROJECT_ROOT}")
    print(f"[map] shared chunk cache: {cache}")
    print(f"[map] downloader: {chunks.downloader.name if chunks.downloader.available else 'NOT FOUND'}")
    if chunks.downloader.error:
        print(f"[map] downloader note: {chunks.downloader.error}")

    TrafficVisualizer(
        network, simulation, chunks,
        initial_vehicles=max(0, args.vehicles),
        width=max(640, args.width),
        height=max(480, args.height),
    ).run()


if __name__ == "__main__":
    main()
