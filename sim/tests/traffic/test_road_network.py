import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.append(str(SRC))


from traffic.road_network import RoadNetwork


network = RoadNetwork()

network.load_osm(
    "sim/cache/osm_chunks/stuttgart/chunk_0_0.osm"
)


print("Nodes:", len(network.nodes))
print("Roads:", len(network.roads))


print()
print("Some roads:")


for road in list(network.roads.values())[:10]:

    print(
        road.id,
        road.highway,
        road.name,
        road.maxspeed,
        road.lanes,
        road.oneway
    )
    
first_node = next(iter(network.nodes.values()))

print()
print("First node:")
print("id:", first_node.id)
print("lat:", first_node.lat)
print("lon:", first_node.lon)
print("x:", first_node.x)
print("y:", first_node.y)



print()
print("Segments:", len(network.segments))

print()
print("First segments:")

for segment in network.segments[:10]:
    print(
        segment.u,
        "->",
        segment.v,
        segment.name,
        segment.highway,
        segment.oneway
    )
    
    
    
print()
print("First segments with lengths:")

for segment in network.segments[:10]:
    print(
        segment.u,
        "->",
        segment.v,
        segment.name,
        f"{segment.length:.2f} m"
    )
    
    
print()
print("First segments with speed limits:")

for segment in network.segments[:10]:
    print(
        segment.name,
        "raw:",
        segment.maxspeed,
        "m/s:",
        segment.speed_limit
    )
    
    
    
print()
print("Traffic lights:")

count = 0

for node in network.nodes.values():

    if node.traffic_light:
        print(
            node.id,
            node.x,
            node.y
        )

        count += 1

print("Total:", count)



print()
print("Traffic light objects:", len(network.traffic_lights))

for node_id, light in network.traffic_lights.items():
    print(
        node_id,
        light.state
    )
    
    
print()
print("Intersections:", len(network.intersections))

for intersection in network.intersections:

    print(
        "Intersection:",
        intersection.id
    )

    print(
        "Signals:",
        intersection.signal_nodes
    )
    
    
    
print()
print("Intersections:", len(network.intersections))

for intersection in network.intersections:

    print()
    print("Intersection:", intersection.id)
    print("Signals:", intersection.signal_nodes)
    print("Phase A:", intersection.phase_a)
    print("Phase B:", intersection.phase_b)
    
    
    
network = RoadNetwork()

network.load_area(
    min_cx=-2,
    max_cx=2,
    min_cy=-2,
    max_cy=2
)

print("Nodes:", len(network.nodes))
print("Roads:", len(network.roads))
print("Segments:", len(network.segments))
print("Intersections:", len(network.intersections))


print()
print("Intersections:", len(network.intersections))

for intersection in network.intersections:

    print()
    print("Intersection:", intersection.id)
    print("Signals:", intersection.signal_nodes)
    print("Phase A:", intersection.phase_a)
    print("Phase B:", intersection.phase_b)
    
    