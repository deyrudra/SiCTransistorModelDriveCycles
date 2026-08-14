import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.append(str(SRC))



from traffic.traffic_light import TrafficLight


light = TrafficLight(
    node_id=123,
    green_time=5.0,
    yellow_time=2.0,
    red_time=5.0
)


simulation_time = 0.0

for i in range(15):

    print(
        f"time={simulation_time:.1f}",
        "state=",
        light.state
    )

    light.update(1.0)

    simulation_time += 1.0
    
    
