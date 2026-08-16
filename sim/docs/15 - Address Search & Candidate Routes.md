# Address Search & Candidate Routes

I've created three files:

1. `candidate_routes.py`
2. `address_search.py`
3. `address_route_search.py`

The `candidate_routes.py` takes in a start node and end node, and returns several alternative routes.

- The first route is the normal best route, after that it penalizes roads already used by earlier candidates so later routes are pushed into different nodes.

The `address_search.py` script lets you search for an actual Stuttgart address, or place name such as: "Schlossplatz, Stuttgart" or "Mercedesstrasse 100, Stuttgart", then it returns latitude and longitude results.

- It also caches results in `sim/cache/geocoding/stuttgart-nominatim.json`

The `address_route_service.py` connects the two systems together.

1. It takes in the start address, finds the nearest drivable node in the road graph.
2. It takes in the destination address, finds the nearest drivable node in the road graph.
3. Creates the points A and B and runs `candidate_route.py` to generate several candidate routes.

Example Usage:

```python
from drive_cycles.address_route_service import (
    plan_candidate_routes_from_addresses,
)

plan = plan_candidate_routes_from_addresses(
    network,
    "Schlossplatz, Stuttgart",
    "Mercedesstraße 100, Stuttgart",
    project_root=PROJECT_ROOT,
    candidate_count=3,
)

print(plan.start_match.display_name)
print(plan.end_match.display_name)

print(plan.start_snap_distance_m)
print(plan.end_snap_distance_m)

for candidate in plan.candidates:
    route = candidate.route

    print(
        candidate.candidate_index,
        route.distance_m,
        route.estimated_time_s,
    )
```



Additionally, a GUI was created to easily interact with these scripts. It could be seen when launching `visualization.py`.



