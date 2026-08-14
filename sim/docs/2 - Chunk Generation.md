# Chunk Generation

Using Stuttgart's drivable OSM road network, we will download it, cache it locally, and divide it into deterministic 250 m x 250 m chunks.

The lat/lon coordinates will be converted to their UTM (Universal Transverse Mercator) coordinates.

- This flattens the round Earther onto a grid using meters instead of degrees.

Pipeline:

```
OSM (OpenStreetMap)
lat/lon
        |
        ▼
UTM Coordinates Conversion
meters
        |
        ▼
Local Stuttgart Coordinates
x = east, y = north
        |
        ▼
250 m x 250 m grid
        |
        ▼
chunk IDs (cx, cy)
```

Prerequisites:

```bash
pip install pyproj
pip install osmnx
pip install matplotlib
pip install requests
```

**Step 1**

The `grid.py` does two things their own question.

\- world_to_chunk(x,y): returns the chunk that contains the world coordinates (x,y)

\- chunk_bounds(cx,cy): returns the bounds of a chunk in world coordinates



**Step 2**

The `projection.py` converts real Stuttgart GPS coordinates into local meter coordinates.

- `latlon_to_local(lat, lon)` -> returns x,y
- `local_to_latlon(x,y)` -> returns lat/lon

We want to go from latitude/longitude to local x/y in meters.

WGS: World Geodetic System 1984 (WGS84) is a standard for GPS coordinates.

\- It uses latitude and longitude to specify locations on the Earth's surface.



**Step 3**

In `projection.py` creating a function that takes a given chunk ID and get the GPS bounding box for that 250m square.

`local_bounds_to_latlon(min_x, min_y, max_x, max_y)`



**Step 4**

The `download_chunk.py` is a script created in `tools/osm/download_chunk.py`.  This downloads the OSM data of a particular chunk via the OSM_API: "https://api.openstreetmap.org/api/0.6/map" 

There are two main functions:

- `download_chunk(cx,cy)`: downloads a single chunk which is relative to the origin point which is defined in `projection.py`
- `save_chunk(cx, cy, osm_data)`: saves the osm chunk data into cache.



**Step 5**

Inspecting one node and one edge so we can understand what `OSMnx` gave us before saving anything. We are performing this test in `test/chunks/inspect_chunk.py`.

- This is in the main of the download_chunk.py file, and when run as a standalone file it will output the inspection.



**Step 6**

Adding the `get_chunk()` function to `download_chunk.py`, so that we can create a `download_area.py` which downloads the surrounding chunks to a particular chunk.

- `download_area(min_cx, max_cx, min_cy, max_cy)`: The inputs define a box, and that entire box will be downloaded, or checked for in cache.

The test for this is in the `download_chunk.py` file, running it as a standalone will perform the test.



