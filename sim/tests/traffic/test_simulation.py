import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.append(str(SRC))

from traffic.road_network import RoadNetwork
from traffic.simulation import Simulation


network = RoadNetwork()

network.load_osm(
    "sim/cache/osm_chunks/stuttgart/chunk_0_0.osm"
)

simulation = Simulation(network)

simulation.speed = 10.0


for i in range(10):
    simulation.update(0.1)

    print(
        "Simulation time:",
        simulation.time
    )