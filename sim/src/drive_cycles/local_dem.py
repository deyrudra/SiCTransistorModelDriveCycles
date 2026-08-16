from __future__ import annotations

"""
Local SRTM/Skadi HGT elevation reader.

Supports 1-degree .hgt tiles such as:
    N48E009.hgt

No third-party packages are required.

The tile is memory-mapped and sampled with bilinear interpolation. HGT files
store signed 16-bit big-endian elevations in metres, ordered north-to-south.
"""

from dataclasses import dataclass
import math
import mmap
from pathlib import Path
import struct
import threading
from typing import Optional


VOID_ELEVATION = -32768


def tile_name_for_latlon(latitude: float, longitude: float) -> str:
    lat_floor = math.floor(latitude)
    lon_floor = math.floor(longitude)

    lat_prefix = "N" if lat_floor >= 0 else "S"
    lon_prefix = "E" if lon_floor >= 0 else "W"

    return (
        f"{lat_prefix}{abs(lat_floor):02d}"
        f"{lon_prefix}{abs(lon_floor):03d}"
    )


@dataclass
class _OpenTile:
    path: Path
    file_handle: object
    mmap_handle: mmap.mmap
    samples: int
    lat_floor: int
    lon_floor: int

    def close(self) -> None:
        try:
            self.mmap_handle.close()
        finally:
            self.file_handle.close()


class LocalHgtDem:
    def __init__(
        self,
        dem_dir: str | Path,
    ) -> None:
        self.dem_dir = Path(dem_dir)
        self.dem_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._tiles: dict[str, _OpenTile] = {}
        self._lock = threading.RLock()

    @property
    def dataset(self) -> str:
        return "local_hgt"

    def close(self) -> None:
        with self._lock:
            for tile in self._tiles.values():
                tile.close()
            self._tiles.clear()

    def available_tile_names(self) -> list[str]:
        return sorted(
            path.stem
            for path in self.dem_dir.glob("*.hgt")
        )

    def tile_path(
        self,
        latitude: float,
        longitude: float,
    ) -> Path:
        return (
            self.dem_dir
            / f"{tile_name_for_latlon(latitude, longitude)}.hgt"
        )

    def has_tile(
        self,
        latitude: float,
        longitude: float,
    ) -> bool:
        return self.tile_path(
            latitude,
            longitude,
        ).is_file()

    def _open_tile(
        self,
        latitude: float,
        longitude: float,
    ) -> _OpenTile:
        name = tile_name_for_latlon(
            latitude,
            longitude,
        )

        with self._lock:
            cached = self._tiles.get(name)

            if cached is not None:
                return cached

            path = self.dem_dir / f"{name}.hgt"

            if not path.is_file():
                raise FileNotFoundError(
                    f"Local DEM tile is missing: {path}"
                )

            size_bytes = path.stat().st_size

            if size_bytes % 2 != 0:
                raise ValueError(
                    f"Invalid HGT size for {path}: {size_bytes} bytes"
                )

            sample_count = size_bytes // 2
            samples = math.isqrt(
                sample_count
            )

            if samples * samples != sample_count:
                raise ValueError(
                    f"HGT file does not contain a square raster: {path}"
                )

            # Common SRTM resolutions:
            # 1201 -> 3 arc-second
            # 3601 -> 1 arc-second
            if samples < 2:
                raise ValueError(
                    f"HGT raster is too small: {path}"
                )

            lat_floor = math.floor(latitude)
            lon_floor = math.floor(longitude)

            handle = path.open("rb")
            mapped = mmap.mmap(
                handle.fileno(),
                length=0,
                access=mmap.ACCESS_READ,
            )

            tile = _OpenTile(
                path=path,
                file_handle=handle,
                mmap_handle=mapped,
                samples=samples,
                lat_floor=lat_floor,
                lon_floor=lon_floor,
            )

            self._tiles[name] = tile
            return tile

    @staticmethod
    def _sample_raw(
        tile: _OpenTile,
        row: int,
        col: int,
    ) -> int:
        row = max(
            0,
            min(
                tile.samples - 1,
                int(row),
            ),
        )

        col = max(
            0,
            min(
                tile.samples - 1,
                int(col),
            ),
        )

        offset = (
            (row * tile.samples + col)
            * 2
        )

        return struct.unpack_from(
            ">h",
            tile.mmap_handle,
            offset,
        )[0]

    @staticmethod
    def _valid_value(
        value: int,
    ) -> bool:
        return value != VOID_ELEVATION

    def elevation_m(
        self,
        latitude: float,
        longitude: float,
    ) -> Optional[float]:
        tile = self._open_tile(
            latitude,
            longitude,
        )

        # HGT tiles include both boundary rows/columns.
        scale = tile.samples - 1

        x = (
            longitude
            - tile.lon_floor
        ) * scale

        # Raster rows are north -> south.
        y = (
            (tile.lat_floor + 1)
            - latitude
        ) * scale

        x = max(
            0.0,
            min(
                float(scale),
                x,
            ),
        )

        y = max(
            0.0,
            min(
                float(scale),
                y,
            ),
        )

        x0 = int(
            math.floor(x)
        )
        y0 = int(
            math.floor(y)
        )

        x1 = min(
            scale,
            x0 + 1,
        )
        y1 = min(
            scale,
            y0 + 1,
        )

        tx = x - x0
        ty = y - y0

        values = [
            (
                self._sample_raw(
                    tile,
                    y0,
                    x0,
                ),
                (1.0 - tx) * (1.0 - ty),
            ),
            (
                self._sample_raw(
                    tile,
                    y0,
                    x1,
                ),
                tx * (1.0 - ty),
            ),
            (
                self._sample_raw(
                    tile,
                    y1,
                    x0,
                ),
                (1.0 - tx) * ty,
            ),
            (
                self._sample_raw(
                    tile,
                    y1,
                    x1,
                ),
                tx * ty,
            ),
        ]

        weighted_sum = 0.0
        weight_sum = 0.0

        for value, weight in values:
            if not self._valid_value(
                value
            ):
                continue

            weighted_sum += (
                float(value)
                * weight
            )

            weight_sum += weight

        if weight_sum <= 0.0:
            return None

        return weighted_sum / weight_sum
