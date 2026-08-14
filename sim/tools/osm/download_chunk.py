import requests
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "chunks"
sys.path.append(str(SRC_DIR))

from grid import chunk_bounds
from projection import local_bounds_to_latlon


OSM_API = "https://api.openstreetmap.org/api/0.6/map"


def download_chunk(cx, cy):
    min_x, min_y, max_x, max_y = chunk_bounds(cx, cy)

    south, west, north, east = local_bounds_to_latlon(
        min_x,
        min_y,
        max_x,
        max_y
    )

    print("Downloading chunk:", cx, cy)

    print("West: ", west)
    print("South:", south)
    print("East: ", east)
    print("North:", north)

    params = {
        "bbox": f"{west},{south},{east},{north}"
    }

    headers = {
        "User-Agent": "SiCTransistorModelDriveCycles/1.0"
    }

    response = requests.get(
        OSM_API,
        params=params,
        headers=headers
    )

    response.raise_for_status()

    return response.text

def chunk_path(cx, cy):
    folder = Path(__file__).resolve().parents[2] / "cache" / "osm_chunks" / "stuttgart"

    return folder / f"chunk_{cx}_{cy}.osm"

def chunk_exists(cx, cy):
    return chunk_path(cx, cy).exists()

def save_chunk(cx, cy, osm_data):
    filename = chunk_path(cx, cy)

    filename.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(filename, "w", encoding="utf-8") as file:
        file.write(osm_data)

    print("Saved:", filename)

def get_chunk(cx, cy):
    filename = chunk_path(cx, cy)

    if filename.exists():
        print("Using cached chunk:", filename)
        return filename

    osm_data = download_chunk(cx, cy)

    save_chunk(
        cx,
        cy,
        osm_data
    )

    return filename

if __name__ == "__main__":
    cx = 0
    cy = 0

    if chunk_exists(cx, cy):
        print("Chunk already cached:", chunk_path(cx, cy))

    else:
        osm_data = download_chunk(cx, cy)

        save_chunk(
            cx,
            cy,
            osm_data
        )
        
