import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.append(str(SRC))

from traffic.road_network import RoadNetwork
from traffic.vehicle import Vehicle
from traffic.simulation import Simulation

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

simulation = Simulation(network)

segment = network.segments[0]

car1 = Vehicle(
    vehicle_id=1,
    segment=segment,
    network=network,
    simulation=simulation
)

car2 = Vehicle(
    vehicle_id=2,
    segment=segment,
    network=network,
    simulation=simulation
)

car1.position = 10.0
car2.position = 0.0

simulation.add_vehicle(car1)
simulation.add_vehicle(car2)


dt = 0.1

for i in range(1000):

    simulation.update(dt)

    print(
        round(simulation.time, 1),
        "car1:",
        round(car1.position, 2),
        round(car1.speed, 2),
        "car2:",
        round(car2.position, 2),
        round(car2.speed, 2)
    )