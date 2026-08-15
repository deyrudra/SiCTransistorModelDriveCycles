from __future__ import annotations

from dataclasses import dataclass
import math

from drive_cycles.vehicle_config import VehicleDynamicsConfig


G_MPS2 = 9.80665
AIR_DENSITY_KG_M3 = 1.225
LOW_SPEED_POWER_EPS_MPS = 1.0


@dataclass(frozen=True)
class LongitudinalForces:
    requested_acceleration_mps2: float
    actual_acceleration_mps2: float
    inertial_force_n: float
    rolling_force_n: float
    aerodynamic_force_n: float
    grade_force_n: float
    requested_wheel_force_n: float
    actual_wheel_force_n: float
    requested_wheel_power_w: float
    actual_wheel_power_w: float
    dc_power_w: float
    friction_brake_power_w: float
    propulsion_power_limited: bool
    regen_power_limited: bool


class LongitudinalVehicleModel:
    def __init__(
        self,
        config: VehicleDynamicsConfig,
        *,
        air_density_kg_m3: float = AIR_DENSITY_KG_M3,
    ) -> None:
        self.config = config
        self.air_density_kg_m3 = float(air_density_kg_m3)

    def resistance_forces(
        self,
        *,
        speed_mps: float,
        grade_deg: float,
    ) -> tuple[float, float, float]:
        v = max(0.0, float(speed_mps))
        theta = math.radians(float(grade_deg))
        m = self.config.mass_kg

        rolling = (
            self.config.rolling_resistance_coefficient
            * m
            * G_MPS2
            * math.cos(theta)
        )

        aerodynamic = (
            0.5
            * self.air_density_kg_m3
            * self.config.drag_coefficient
            * self.config.frontal_area_m2
            * v
            * v
        )

        grade = m * G_MPS2 * math.sin(theta)

        return rolling, aerodynamic, grade

    def step(
        self,
        *,
        speed_mps: float,
        requested_acceleration_mps2: float,
        grade_deg: float,
    ) -> LongitudinalForces:
        v = max(0.0, float(speed_mps))

        requested_a = min(
            float(requested_acceleration_mps2),
            self.config.max_acceleration_mps2,
        )
        requested_a = max(
            requested_a,
            -self.config.emergency_braking_mps2,
        )

        rolling, aerodynamic, grade = self.resistance_forces(
            speed_mps=v,
            grade_deg=grade_deg,
        )

        resistance = rolling + aerodynamic + grade

        requested_wheel_force = (
            self.config.mass_kg * requested_a
            + resistance
        )

        requested_wheel_power = requested_wheel_force * v
        actual_wheel_force = requested_wheel_force

        propulsion_limited = False
        regen_limited = False
        friction_brake_power = 0.0

        effective_speed = max(v, LOW_SPEED_POWER_EPS_MPS)

        if requested_wheel_force >= 0.0:
            max_wheel_power = (
                self.config.max_propulsion_power_w
                * self.config.drivetrain_efficiency
            )
            max_wheel_force = max_wheel_power / effective_speed

            if actual_wheel_force > max_wheel_force:
                actual_wheel_force = max_wheel_force
                propulsion_limited = True

        else:
            if self.config.regenerative_efficiency > 0.0:
                max_regen_wheel_power = (
                    self.config.max_regen_power_w
                    / self.config.regenerative_efficiency
                )
            else:
                max_regen_wheel_power = 0.0

            max_regen_force = (
                max_regen_wheel_power
                / effective_speed
            )

            brake_force = abs(actual_wheel_force)

            if brake_force > max_regen_force:
                regen_limited = True
                friction_brake_power = (
                    brake_force - max_regen_force
                ) * v

        actual_acceleration = (
            actual_wheel_force - resistance
        ) / self.config.mass_kg

        actual_acceleration = min(
            actual_acceleration,
            self.config.max_acceleration_mps2,
        )
        actual_acceleration = max(
            actual_acceleration,
            -self.config.emergency_braking_mps2,
        )

        actual_wheel_power = actual_wheel_force * v

        if actual_wheel_power >= 0.0:
            dc_power = (
                actual_wheel_power
                / max(self.config.drivetrain_efficiency, 1e-9)
            )
        else:
            dc_power = (
                actual_wheel_power
                * self.config.regenerative_efficiency
            )
            dc_power = max(
                dc_power,
                -self.config.max_regen_power_w,
            )

        return LongitudinalForces(
            requested_acceleration_mps2=requested_a,
            actual_acceleration_mps2=actual_acceleration,
            inertial_force_n=self.config.mass_kg * actual_acceleration,
            rolling_force_n=rolling,
            aerodynamic_force_n=aerodynamic,
            grade_force_n=grade,
            requested_wheel_force_n=requested_wheel_force,
            actual_wheel_force_n=actual_wheel_force,
            requested_wheel_power_w=requested_wheel_power,
            actual_wheel_power_w=actual_wheel_power,
            dc_power_w=dc_power,
            friction_brake_power_w=friction_brake_power,
            propulsion_power_limited=propulsion_limited,
            regen_power_limited=regen_limited,
        )
