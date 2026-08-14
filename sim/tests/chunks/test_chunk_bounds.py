import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent / ".." / ".." / "src" / "chunks"))

from grid import chunk_bounds
from projection import local_bounds_to_latlon

cx = 0
cy = 0

min_x, min_y, max_x, max_y = chunk_bounds(cx, cy)

south, west, north, east = local_bounds_to_latlon(
    min_x,
    min_y,
    max_x,
    max_y
)

print("Chunk:", cx, cy)

print("Local bounds:")
print(min_x, min_y, max_x, max_y)

print("GPS bounds:")
print("South:", south)
print("West: ", west)
print("North:", north)
print("East: ", east)