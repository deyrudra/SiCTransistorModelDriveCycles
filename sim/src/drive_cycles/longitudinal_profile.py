from __future__ import annotations

"""
Offline longitudinal mission-profile analysis.

Inputs:
- recorded drive-cycle CSV: time_s,v_mps,grade_deg
- vehicle YAML

Outputs per sample:
- speed / acceleration / grade
- inertial, rolling, aerodynamic, and grade forces
- total wheel force
- wheel power
- DC-bus power
- friction braking power

This stage is intentionally independent of the live Pygame simulation.
"""

from dataclasses import dataclass
import csv
import math
from pathlib import Path

from drive_cycles.drive_cycle_loader import (
    RecordedMissionProfile,
    build_mission_profile,
    load_drive_cycle,
)
from drive_cycles.vehicle_config import (
    VehicleDynamicsConfig,
    load_vehicle_config,
)
from drive_cycles.regen_model import split_regen_and_friction


G_MPS2 = 9.80665
AIR_DENSITY_KG_M3 = 1.225


@dataclass(frozen=True)
class LongitudinalSample:
    time_s: float
    speed_mps: float
    acceleration_mps2: float
    grade_deg: float

    force_inertial_n: float
    force_rolling_n: float
    force_aero_n: float
    force_grade_n: float
    force_total_n: float

    wheel_power_w: float
    dc_power_w: float
    friction_brake_power_w: float


@dataclass(frozen=True)
class LongitudinalResult:
    samples: tuple[LongitudinalSample, ...]
    source_cycle: Path
    vehicle_name: str
    distance_km: float
    traction_energy_kwh: float
    recovered_energy_kwh: float
    friction_brake_energy_kwh: float
    peak_wheel_power_kw: float
    peak_dc_power_kw: float
    peak_regen_power_kw: float


def _forces(
    config: VehicleDynamicsConfig,
    *,
    speed_mps: float,
    acceleration_mps2: float,
    grade_deg: float,
) -> tuple[float, float, float, float, float]:
    theta = math.radians(grade_deg)
    m = config.mass_kg
    v = max(0.0, speed_mps)

    inertial = m * acceleration_mps2

    rolling = (
        config.rolling_resistance_coefficient
        * m
        * G_MPS2
        * math.cos(theta)
    )

    aero = (
        0.5
        * AIR_DENSITY_KG_M3
        * config.drag_coefficient
        * config.frontal_area_m2
        * v
        * v
    )

    grade = (
        m
        * G_MPS2
        * math.sin(theta)
    )

    total = inertial + rolling + aero + grade

    return inertial, rolling, aero, grade, total


def analyze_longitudinal_profile(
    profile: RecordedMissionProfile,
    config: VehicleDynamicsConfig,
) -> LongitudinalResult:
    rows: list[LongitudinalSample] = []

    traction_energy_j = 0.0
    recovered_energy_j = 0.0
    friction_energy_j = 0.0
    distance_m = 0.0

    peak_wheel_power_w = 0.0
    peak_dc_power_w = 0.0
    peak_regen_power_w = 0.0

    previous_time = profile.time_s[0]
    previous_speed = profile.speed_mps[0]

    for i, (
        t,
        v,
        a,
        grade_deg,
    ) in enumerate(
        zip(
            profile.time_s,
            profile.speed_mps,
            profile.acceleration_mps2,
            profile.grade_deg,
        )
    ):
        (
            force_inertial,
            force_rolling,
            force_aero,
            force_grade,
            force_total,
        ) = _forces(
            config,
            speed_mps=v,
            acceleration_mps2=a,
            grade_deg=grade_deg,
        )

        wheel_power = force_total * v

        friction_brake_power = 0.0

        if wheel_power >= 0.0:
            requested_dc_power = (
                wheel_power
                / max(
                    config.drivetrain_efficiency,
                    1e-9,
                )
            )

            dc_power = min(
                requested_dc_power,
                config.max_propulsion_power_w,
            )

            # Report actual available wheel/DC power. If this recorded cycle
            # demanded more than the configured propulsion limit, downstream
            # stages can identify the inconsistency from the clipping.
            if requested_dc_power > config.max_propulsion_power_w:
                wheel_power = (
                    config.max_propulsion_power_w
                    * config.drivetrain_efficiency
                )

        else:
            split = split_regen_and_friction(
                speed_mps=v,
                braking_wheel_power_w=abs(wheel_power),
                regenerative_efficiency=config.regenerative_efficiency,
                max_regen_dc_power_w=config.max_regen_power_w,
                cutoff_speed_mps=config.regen_cutoff_speed_mps,
                full_regen_speed_mps=config.regen_full_speed_mps,
            )

            # Negative DC power means energy returned to the DC bus.
            dc_power = -split.recovered_dc_power_w
            friction_brake_power = split.friction_brake_power_w

        rows.append(
            LongitudinalSample(
                time_s=t,
                speed_mps=v,
                acceleration_mps2=a,
                grade_deg=grade_deg,
                force_inertial_n=force_inertial,
                force_rolling_n=force_rolling,
                force_aero_n=force_aero,
                force_grade_n=force_grade,
                force_total_n=force_total,
                wheel_power_w=wheel_power,
                dc_power_w=dc_power,
                friction_brake_power_w=friction_brake_power,
            )
        )

        peak_wheel_power_w = max(
            peak_wheel_power_w,
            wheel_power,
        )

        if dc_power >= 0.0:
            peak_dc_power_w = max(
                peak_dc_power_w,
                dc_power,
            )
        else:
            peak_regen_power_w = max(
                peak_regen_power_w,
                abs(dc_power),
            )

        if i > 0:
            dt = t - previous_time

            # Distance from trapezoidal speed integration.
            distance_m += (
                0.5
                * (previous_speed + v)
                * dt
            )

            if dc_power >= 0.0:
                traction_energy_j += dc_power * dt
            else:
                recovered_energy_j += abs(dc_power) * dt

            friction_energy_j += (
                friction_brake_power
                * dt
            )

        previous_time = t
        previous_speed = v

    return LongitudinalResult(
        samples=tuple(rows),
        source_cycle=profile.source_path,
        vehicle_name=config.name,
        distance_km=distance_m / 1000.0,
        traction_energy_kwh=traction_energy_j / 3_600_000.0,
        recovered_energy_kwh=recovered_energy_j / 3_600_000.0,
        friction_brake_energy_kwh=friction_energy_j / 3_600_000.0,
        peak_wheel_power_kw=peak_wheel_power_w / 1000.0,
        peak_dc_power_kw=peak_dc_power_w / 1000.0,
        peak_regen_power_kw=peak_regen_power_w / 1000.0,
    )


def write_longitudinal_csv(
    result: LongitudinalResult,
    output_path: str | Path,
) -> Path:
    path = Path(output_path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        handle.write(
            f"# source_cycle={result.source_cycle}\n"
        )
        handle.write(
            f"# vehicle_config={result.vehicle_name}\n"
        )
        handle.write(
            f"# distance_km={result.distance_km:.9f}\n"
        )
        handle.write(
            f"# traction_energy_kwh={result.traction_energy_kwh:.9f}\n"
        )
        handle.write(
            f"# recovered_energy_kwh={result.recovered_energy_kwh:.9f}\n"
        )

        writer = csv.writer(handle)

        writer.writerow(
            [
                "time_s",
                "v_mps",
                "accel_mps2",
                "grade_deg",
                "force_inertial_n",
                "force_rolling_n",
                "force_aero_n",
                "force_grade_n",
                "force_total_n",
                "wheel_power_w",
                "dc_power_w",
                "friction_brake_power_w",
            ]
        )

        for row in result.samples:
            writer.writerow(
                [
                    f"{row.time_s:.6f}",
                    f"{row.speed_mps:.6f}",
                    f"{row.acceleration_mps2:.6f}",
                    f"{row.grade_deg:.6f}",
                    f"{row.force_inertial_n:.6f}",
                    f"{row.force_rolling_n:.6f}",
                    f"{row.force_aero_n:.6f}",
                    f"{row.force_grade_n:.6f}",
                    f"{row.force_total_n:.6f}",
                    f"{row.wheel_power_w:.6f}",
                    f"{row.dc_power_w:.6f}",
                    f"{row.friction_brake_power_w:.6f}",
                ]
            )

    return path


def run_longitudinal_analysis(
    drive_cycle_path: str | Path,
    vehicle_config_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> tuple[LongitudinalResult, Path]:
    cycle = load_drive_cycle(
        drive_cycle_path
    )

    profile = build_mission_profile(
        cycle
    )

    config = load_vehicle_config(
        vehicle_config_path
    )

    result = analyze_longitudinal_profile(
        profile,
        config,
    )

    source = Path(drive_cycle_path)

    if output_path is None:
        output_path = source.with_name(
            source.stem + "_power.csv"
        )

    written = write_longitudinal_csv(
        result,
        output_path,
    )

    return result, written
