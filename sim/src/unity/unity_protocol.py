from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = 1


# ----------------------------------------------------------------------
# Base helpers
# ----------------------------------------------------------------------

def encode_message(message: dict[str, Any]) -> bytes:
    """
    Encode one protocol message as newline-delimited UTF-8 JSON.
    """
    payload = json.dumps(
        message,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return (payload + "\n").encode("utf-8")


def decode_message(data: bytes | str) -> dict[str, Any]:
    """
    Decode one complete JSON protocol message.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8")

    message = json.loads(data.strip())

    if not isinstance(message, dict):
        raise ValueError("Protocol message must be a JSON object")

    if "type" not in message:
        raise ValueError("Protocol message has no 'type' field")

    return message


# ----------------------------------------------------------------------
# Handshake
# ----------------------------------------------------------------------

def hello_message() -> dict[str, Any]:
    return {
        "type": "hello",
        "protocol_version": PROTOCOL_VERSION,
        "coordinate_system": "stuttgart_local_meters",
    }


# ----------------------------------------------------------------------
# GLB chunks
# ----------------------------------------------------------------------

def chunk_load_message(
    cx: int,
    cy: int,
    path: str | Path,
) -> dict[str, Any]:

    return {
        "type": "chunk_load",
        "cx": int(cx),
        "cy": int(cy),
        "path": str(Path(path).resolve()),
    }


def chunk_unload_message(
    cx: int,
    cy: int,
) -> dict[str, Any]:

    return {
        "type": "chunk_unload",
        "cx": int(cx),
        "cy": int(cy),
    }


# ----------------------------------------------------------------------
# Camera
# ----------------------------------------------------------------------

@dataclass
class CameraState:
    x: float
    y: float

    zoom: float = 1.0

    rotation: float = 0.0


def camera_state_message(
    camera: CameraState,
) -> dict[str, Any]:

    return {
        "type": "camera_state",
        **asdict(camera),
    }


# ----------------------------------------------------------------------
# Vehicles
# ----------------------------------------------------------------------

@dataclass
class VehicleState:
    id: int

    x: float
    y: float

    heading: float

    speed: float


def vehicle_state(
    vehicle,
) -> VehicleState:
    """
    Convert the project's Vehicle object into its network representation.
    """

    x, y = vehicle.get_position()

    start = vehicle.network.nodes[vehicle.segment.u]
    end = vehicle.network.nodes[vehicle.segment.v]

    import math

    heading = math.atan2(
        end.y - start.y,
        end.x - start.x,
    )

    return VehicleState(
        id=int(vehicle.id),
        x=float(x),
        y=float(y),
        heading=float(heading),
        speed=float(vehicle.speed),
    )


def vehicles_message(
    vehicles,
) -> dict[str, Any]:

    return {
        "type": "vehicles",
        "vehicles": [
            asdict(vehicle_state(vehicle))
            for vehicle in vehicles
        ],
    }


# ----------------------------------------------------------------------
# Traffic lights
# ----------------------------------------------------------------------

@dataclass
class TrafficLightState:
    node_id: int

    x: float
    y: float

    state: str


def traffic_light_states(network) -> list[TrafficLightState]:

    result: list[TrafficLightState] = []

    for intersection in network.intersections:

        for node_id in intersection.signal_nodes:

            node = network.nodes.get(node_id)

            if node is None:
                continue

            state = intersection.get_signal_state(node_id)

            if state is None:
                continue

            result.append(
                TrafficLightState(
                    node_id=int(node_id),
                    x=float(node.x),
                    y=float(node.y),
                    state=state,
                )
            )

    return result


def traffic_lights_message(
    network,
) -> dict[str, Any]:

    return {
        "type": "traffic_lights",
        "lights": [
            asdict(light)
            for light in traffic_light_states(network)
        ],
    }


# ----------------------------------------------------------------------
# Simulation state
# ----------------------------------------------------------------------

def simulation_state_message(
    simulation,
) -> dict[str, Any]:

    return {
        "type": "simulation_state",
        "time": float(simulation.time),
        "speed": float(simulation.speed),
        "vehicle_count": len(simulation.vehicles),
    }


# ----------------------------------------------------------------------
# Incoming Unity commands
# ----------------------------------------------------------------------

def parse_unity_command(
    message: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """
    Validate and normalize messages sent from Unity to Python.
    """

    message_type = message.get("type")

    if not isinstance(message_type, str):
        raise ValueError("Unity message is missing a valid type")

    payload = dict(message)
    payload.pop("type", None)

    return message_type, payload


# ----------------------------------------------------------------------
# Test
# ----------------------------------------------------------------------

if __name__ == "__main__":

    messages = [
        hello_message(),

        chunk_load_message(
            0,
            0,
            "cache/glb_chunks/stuttgart/chunk_0_0.glb",
        ),

        chunk_unload_message(
            -1,
            2,
        ),

        camera_state_message(
            CameraState(
                x=100.0,
                y=250.0,
                zoom=2.0,
            )
        ),
    ]

    for message in messages:

        encoded = encode_message(message)

        print(encoded.decode("utf-8").rstrip())

        decoded = decode_message(encoded)

        assert decoded == message