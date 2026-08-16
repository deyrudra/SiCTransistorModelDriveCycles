# Elevation API Replacement

Previously I was using the `OpenTopoData`, in which I was rate-limited due to it being a public API. Now I will use local elevation data to have unlimited 

I've create three scripts:

`local_dem.py`

- Actual local elevation reader, it reads the `.hgt` terrain tile, looks up elevation for a latitude/longitude.

`download_local_dem.py`

- One time download, you run it to download the Stuttgart DEM tile, `N48E009.hgt`, into the local elevation cache folder.

`elevation_data.py`

- This is the manager already used by the simulation, it first checks the existing JSON elevation cache, and then falls back to local DEM for missing points, caching those results.



