from __future__ import annotations

from typing import Optional

from drive_cycles.vehicle_config import VehicleDynamicsConfig


class EgoVehicle:
    def __init__(
        self,
        vehicle_id: int,
        route_segments,
        network,
        simulation,
        config: VehicleDynamicsConfig,
    ) -> None:
        self.id = int(vehicle_id)
        self.network = network
        self.simulation = simulation
        self.config = config
        self.vehicle_name = config.name

        self.route_segments = tuple(route_segments)
        self.route_index = 0
        self.segment = self.route_segments[0] if self.route_segments else None

        self.position = 0.0
        self.speed = 0.0
        self.acceleration_mps2 = 0.0

        self.max_acceleration_mps2 = config.max_acceleration_mps2
        self.comfortable_braking_mps2 = config.comfortable_braking_mps2
        self.emergency_braking_mps2 = config.emergency_braking_mps2
        self.safe_distance_m = config.safe_distance_m
        self.time_headway_s = config.time_headway_s

        self.arrived = not bool(self.route_segments)

    def get_position(self) -> tuple[float, float]:
        if self.segment is None:
            return 0.0, 0.0

        start = self.network.nodes[self.segment.u]
        end = self.network.nodes[self.segment.v]

        length = max(float(self.segment.length), 1e-9)
        t = max(0.0, min(1.0, self.position / length))

        x = start.x + (end.x - start.x) * t
        y = start.y + (end.y - start.y) * t

        return x, y

    @property
    def progress(self) -> float:
        if not self.route_segments:
            return 1.0

        completed = float(self.route_index)

        if self.segment is not None and self.segment.length > 0.0:
            completed += max(
                0.0,
                min(1.0, self.position / self.segment.length),
            )

        return min(1.0, completed / len(self.route_segments))

    def _traffic_light_state(self) -> Optional[str]:
        if self.segment is None:
            return None

        node_id = self.segment.v

        cache = getattr(self.simulation, "_signal_state_cache", None)
        if cache is not None:
            state = cache.get(node_id)
            if state is not None:
                return state

        for intersection in self.network.intersections:
            state = intersection.get_signal_state(node_id)
            if state is not None:
                return state

        return None

    def _distance_to_vehicle_ahead(self) -> Optional[float]:
        if self.segment is None:
            return None

        nearest = None

        for other in self.simulation.vehicles:
            if getattr(other, "segment", None) is not self.segment:
                continue

            other_position = float(getattr(other, "position", 0.0))

            if other_position <= self.position:
                continue

            distance = other_position - self.position

            if nearest is None or distance < nearest:
                nearest = distance

        return nearest

    def _desired_speed(self) -> float:
        if self.segment is None:
            return 0.0

        speed_limit = getattr(self.segment, "speed_limit", None)

        if speed_limit is None or speed_limit <= 0.0:
            target = 10.0
        else:
            target = float(speed_limit)

        traffic_factor = getattr(
            self.simulation,
            "traffic_speed_factor",
            1.0,
        )

        return target * max(0.12, min(1.0, float(traffic_factor)))

    def _target_acceleration(self) -> float:
        if self.arrived or self.segment is None:
            return 0.0

        desired_speed = self._desired_speed()

        if self.speed < desired_speed:
            accel = self.max_acceleration_mps2
        else:
            accel = -self.comfortable_braking_mps2

        speed_error = desired_speed - self.speed
        if abs(speed_error) < 1.0:
            accel = speed_error

        distance_ahead = self._distance_to_vehicle_ahead()

        if distance_ahead is not None:
            desired_gap = (
                self.safe_distance_m
                + self.speed * self.time_headway_s
            )

            if distance_ahead <= self.safe_distance_m:
                accel = -self.emergency_braking_mps2
            elif distance_ahead < desired_gap:
                ratio = (
                    distance_ahead - self.safe_distance_m
                ) / max(
                    desired_gap - self.safe_distance_m,
                    0.1,
                )
                allowed_speed = desired_speed * max(0.0, min(1.0, ratio))

                if self.speed > allowed_speed:
                    accel = min(
                        accel,
                        -self.comfortable_braking_mps2,
                    )

        light_state = self._traffic_light_state()

        if light_state in ("red", "yellow"):
            distance_to_light = max(
                0.0,
                self.segment.length - self.position,
            )

            stopping_distance = (
                self.speed * self.speed
            ) / max(
                2.0 * self.comfortable_braking_mps2,
                0.1,
            )

            if distance_to_light <= stopping_distance + 5.0:
                accel = min(
                    accel,
                    -self.comfortable_braking_mps2,
                )

            if distance_to_light <= 1.5:
                accel = -self.emergency_braking_mps2

        return accel

    def update(self, dt: float) -> None:
        if self.arrived or self.segment is None:
            self.speed = 0.0
            self.acceleration_mps2 = 0.0
            return

        dt = max(0.0, float(dt))
        if dt <= 0.0:
            return

        old_speed = self.speed
        acceleration = self._target_acceleration()

        self.speed = max(
            0.0,
            self.speed + acceleration * dt,
        )

        desired_speed = self._desired_speed()

        if acceleration >= 0.0:
            self.speed = min(self.speed, desired_speed)

        self.acceleration_mps2 = (
            self.speed - old_speed
        ) / dt

        distance = 0.5 * (old_speed + self.speed) * dt
        self.position += distance

        while (
            self.segment is not None
            and self.position >= self.segment.length
        ):
            light_state = self._traffic_light_state()

            if light_state in ("red", "yellow"):
                self.position = max(
                    0.0,
                    self.segment.length - 0.1,
                )
                self.speed = 0.0
                self.acceleration_mps2 = 0.0
                return

            overflow = self.position - self.segment.length
            self.route_index += 1

            if self.route_index >= len(self.route_segments):
                self.position = self.segment.length
                self.speed = 0.0
                self.acceleration_mps2 = 0.0
                self.arrived = True
                return

            self.segment = self.route_segments[self.route_index]
            self.position = max(0.0, overflow)
