from __future__ import annotations

"""
One-time installer for local Stuttgart HGT elevation tiles.

Default Stuttgart tile:
    N48E009.hgt

Source:
    AWS Open Data Terrain Tiles / Skadi HGT archive.

Run from project root:
    python -m drive_cycles.download_local_dem
"""

import argparse
import gzip
from pathlib import Path
import shutil
import urllib.request


AWS_SKADI_ROOT = (
    "https://s3.amazonaws.com/elevation-tiles-prod/skadi"
)

DEFAULT_TILES = (
    "N48E009",
)


def download_tile(
    tile_name: str,
    destination_dir: Path,
    *,
    overwrite: bool = False,
) -> Path:
    destination_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = (
        destination_dir
        / f"{tile_name}.hgt"
    )

    if (
        destination.is_file()
        and not overwrite
    ):
        return destination

    latitude_band = tile_name[:3]

    url = (
        f"{AWS_SKADI_ROOT}/"
        f"{latitude_band}/"
        f"{tile_name}.hgt.gz"
    )

    gz_path = destination.with_suffix(
        ".hgt.gz.part"
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "SiCTransistorModelDriveCycles-local-dem/1.0"
            )
        },
    )

    print(
        f"[DEM] downloading {tile_name}..."
    )
    print(
        f"[DEM] source: {url}"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=90,
        ) as response:
            with gz_path.open(
                "wb"
            ) as handle:
                shutil.copyfileobj(
                    response,
                    handle,
                    length=1024 * 1024,
                )

        temporary_hgt = destination.with_suffix(
            ".hgt.tmp"
        )

        with gzip.open(
            gz_path,
            "rb",
        ) as source:
            with temporary_hgt.open(
                "wb",
            ) as target:
                shutil.copyfileobj(
                    source,
                    target,
                    length=1024 * 1024,
                )

        temporary_hgt.replace(
            destination
        )

    finally:
        try:
            gz_path.unlink()
        except FileNotFoundError:
            pass

    size_mb = (
        destination.stat().st_size
        / (1024 * 1024)
    )

    print(
        f"[DEM] installed: {destination}"
    )
    print(
        f"[DEM] uncompressed size: {size_mb:.1f} MiB"
    )

    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Install local HGT elevation tiles "
            "for Stuttgart."
        )
    )

    parser.add_argument(
        "--dem-dir",
        type=Path,
        default=None,
        help=(
            "Destination directory. Default: "
            "sim/cache/elevation/dem"
        ),
    )

    parser.add_argument(
        "--tile",
        action="append",
        dest="tiles",
        help=(
            "HGT tile name, e.g. N48E009. "
            "May be supplied multiple times."
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
    )

    args = parser.parse_args()

    if args.dem_dir is None:
        project_root = (
            Path(__file__).resolve()
            .parents[2]
        )

        dem_dir = (
            project_root
            / "cache"
            / "elevation"
            / "dem"
        )
    else:
        dem_dir = args.dem_dir

    tiles = (
        args.tiles
        if args.tiles
        else list(DEFAULT_TILES)
    )

    for tile in tiles:
        download_tile(
            tile.strip().upper(),
            dem_dir,
            overwrite=args.overwrite,
        )

    print()
    print(
        "[DEM] ready. Normal elevation lookup can now run offline."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
