"""
Step 2
This file converts real Stuttgart GPS coordinates into local meter coordinates.

We want to go from latitude/longitude to local x/y in meters.

WGS: World Geodetic System 1984 (WGS84) is a standard for GPS coordinates.
- It uses latitude and longitude to specify locations on the Earth's surface.

Step 3
Creating a function that takes a given chunk ID and get the GPS bounding box for that 250m square.
local_bounds_to_latlon(min_x, min_y, max_x, max_y)


"""

from pyproj import Transformer

# WGS84 GPS coordinates
WGS84 = "EPSG:4326"

# UTM zone for Stuttgart
UTM = "EPSG:32632"

transformer = Transformer.from_crs(
    WGS84,
    UTM,
    always_xy=True
)

inverse_transformer = Transformer.from_crs(
    UTM,
    WGS84,
    always_xy=True
)

# Pick a fixed origin near central Stuttgart
ORIGIN_LAT = 48.7758
ORIGIN_LON = 9.1829

origin_x, origin_y = transformer.transform(
    ORIGIN_LON,
    ORIGIN_LAT
)


def latlon_to_local(lat, lon):
    x, y = transformer.transform(lon, lat)

    local_x = x - origin_x
    local_y = y - origin_y

    return local_x, local_y


def local_to_latlon(x, y):
    world_x = x + origin_x
    world_y = y + origin_y

    lon, lat = inverse_transformer.transform(
        world_x,
        world_y
    )

    return lat, lon
    
# step 3
def local_bounds_to_latlon(min_x, min_y, max_x, max_y):
    south, west = local_to_latlon(min_x, min_y)
    north, east = local_to_latlon(max_x, max_y)

    return south, west, north, east


# Test
if __name__ == "__main__":
    print("Origin:")
    print(latlon_to_local(ORIGIN_LAT, ORIGIN_LON))

    print()

    lat = 48.7800
    lon = 9.1900

    x, y = latlon_to_local(lat, lon)

    print("Local:")
    print(x, y)

    print()

    new_lat, new_lon = local_to_latlon(x, y)

    print("Back to GPS:")
    print(new_lat, new_lon)
    
    