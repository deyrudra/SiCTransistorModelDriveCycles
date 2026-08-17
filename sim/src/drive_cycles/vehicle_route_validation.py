from __future__ import annotations

"""
Vehicle / route layer validation.

This validates mission-profile results before inverter/thermal/reliability work.

For each recorded drive cycle it reports:
- distance and duration
- average / peak speed
- stopped-time fraction
- acceleration / braking statistics
- net DC Wh/km
- traction / recovered energy
- difference from the benchmark stored in the vehicle YAML
- comparison of speed behaviour with WLTC Class 3 aggregate characteristics

Important:
A Stuttgart mission is not a WLTP certification test. The benchmark comparison
is a model sanity/calibration check unless the input speed trace is the actual
WLTC reference cycle.
"""

from dataclasses import dataclass, asdict
import csv
import json
import math
from pathlib import Path
from statistics import mean

import yaml

from drive_cycles.route_summary import analyze_route_summary
from drive_cycles.regen_model import split_regen_and_friction
from drive_cycles.local_dem import LocalHgtDem


# WLTC Class 3 aggregate reference characteristics.
# These values describe the complete four-phase Class 3 cycle and are used here
# only to contextualize route speed behaviour, not to certify consumption.
WLTC_CLASS3_DURATION_S = 1800.0
WLTC_CLASS3_DISTANCE_KM = 23.266
WLTC_CLASS3_AVERAGE_SPEED_KMH = 46.5
WLTC_CLASS3_MAX_SPEED_KMH = 131.3
WLTC_CLASS3_STOPPED_TIME_PERCENT = 13.4


@dataclass(frozen=True)
class VehicleRouteValidation:
    profile_name: str
    source_cycle: str
    vehicle_name: str

    benchmark_name: str
    benchmark_cycle: str
    benchmark_wh_per_km: float

    duration_s: float
    distance_km: float
    average_speed_kmh: float
    peak_speed_kmh: float
    stopped_time_percent: float

    mean_positive_accel_mps2: float
    p95_positive_accel_mps2: float
    mean_braking_mps2: float
    p95_braking_magnitude_mps2: float

    traction_energy_kwh: float
    recovered_energy_kwh: float
    net_dc_energy_kwh: float
    simulated_wh_per_km: float
    energy_error_wh_per_km: float
    energy_error_percent: float
    recovered_fraction_percent: float

    aerodynamic_energy_kwh: float
    rolling_resistance_energy_kwh: float
    inertial_energy_kwh: float
    grade_energy_kwh: float
    positive_wheel_traction_energy_kwh: float
    negative_wheel_energy_kwh: float
    drivetrain_loss_energy_kwh: float
    recovered_regen_energy_breakdown_kwh: float
    friction_brake_energy_breakdown_kwh: float
    regen_capture_efficiency_percent: float
    auxiliary_energy_kwh: float
    net_battery_energy_breakdown_kwh: float
    breakdown_wh_per_km: float

    start_elevation_m: float | None
    end_elevation_m: float | None
    endpoint_dem_delta_m: float | None
    grade_integrated_net_elevation_change_m: float
    total_ascent_m: float
    total_descent_m: float
    max_uphill_grade_deg: float
    max_downhill_grade_deg: float
    mean_abs_grade_deg: float
    distance_weighted_mean_abs_grade_deg: float
    grade_clamp_sample_count: int
    grade_clamp_sample_percent: float
    grade_guard_deg: float
    elevation_smoothing_radius_m: float
    elevation_grade_baseline_m: float
    grade_guard_status: str
    elevation_profile_consistency_error_m: float | None

    wltc_average_speed_delta_kmh: float
    wltc_peak_speed_delta_kmh: float
    wltc_stopped_time_delta_percent: float

    energy_assessment: str
    comparison_scope: str


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)

    if len(ordered) == 1:
        return ordered[0]

    position = (
        (len(ordered) - 1)
        * percentile
    )

    low = int(math.floor(position))
    high = int(math.ceil(position))

    if low == high:
        return ordered[low]

    fraction = position - low

    return (
        ordered[low] * (1.0 - fraction)
        + ordered[high] * fraction
    )


def _load_cycle_samples(
    path: str | Path,
) -> tuple[
    list[float],
    list[float],
    list[float],
]:
    times = []
    speeds = []
    grades = []

    with Path(path).open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        lines = (
            line
            for line in handle
            if not line.lstrip().startswith("#")
        )

        reader = csv.DictReader(
            lines
        )

        for row in reader:
            times.append(
                float(row["time_s"])
            )
            speeds.append(
                float(row["v_mps"])
            )
            grades.append(
                float(row.get("grade_deg", 0.0) or 0.0)
            )

    if len(times) < 2:
        raise ValueError(
            "Mission profile needs at least two samples."
        )

    return times, speeds, grades


def _acceleration_samples(
    times: list[float],
    speeds: list[float],
) -> list[float]:
    accelerations = []

    for index in range(
        len(times)
    ):
        if index == 0:
            dt = times[1] - times[0]
            dv = speeds[1] - speeds[0]

        elif index == len(times) - 1:
            dt = times[-1] - times[-2]
            dv = speeds[-1] - speeds[-2]

        else:
            dt = (
                times[index + 1]
                - times[index - 1]
            )

            dv = (
                speeds[index + 1]
                - speeds[index - 1]
            )

        accelerations.append(
            dv / dt
            if dt > 0.0
            else 0.0
        )

    return accelerations


def _load_validation_benchmark(
    yaml_path: str | Path,
) -> dict:
    with Path(yaml_path).open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = yaml.safe_load(
            handle
        ) or {}

    validation = data.get(
        "validation",
        {},
    )

    benchmark_wh_per_km = validation.get(
        "official_consumption_wh_per_km"
    )

    if benchmark_wh_per_km is None:
        kwh_per_100km = validation.get(
            "official_consumption_kwh_per_100km"
        )

        if kwh_per_100km is not None:
            benchmark_wh_per_km = (
                float(kwh_per_100km)
                * 10.0
            )

    if benchmark_wh_per_km is None:
        raise ValueError(
            "Vehicle YAML has no validation.official_consumption_wh_per_km "
            "or official_consumption_kwh_per_100km field."
        )

    return {
        "name": validation.get(
            "benchmark_name",
            data.get(
                "vehicle",
                {},
            ).get(
                "name",
                "vehicle benchmark",
            ),
        ),
        "cycle": validation.get(
            "certification_cycle",
            "WLTP_combined",
        ),
        "wh_per_km": float(
            benchmark_wh_per_km
        ),
    }



AIR_DENSITY_KG_M3 = 1.225
GRAVITY_MPS2 = 9.80665


def _vehicle_energy_parameters(vehicle_config_path):
    with Path(vehicle_config_path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    vehicle = data.get("vehicle", {})
    powertrain = data.get("powertrain", {})
    auxiliary = data.get("auxiliary", {})

    def pick(mapping, names, default):
        for name in names:
            if mapping.get(name) is not None:
                return float(mapping[name])
        return float(default)

    aux_w = pick(
        auxiliary,
        ("base_power_w", "auxiliary_power_w"),
        pick(
            powertrain,
            ("auxiliary_power_w", "base_auxiliary_power_w"),
            pick(vehicle, ("auxiliary_power_w",), 0.0),
        ),
    )

    return {
        "mass_kg": pick(vehicle, ("mass_kg",), 1822.0),
        "frontal_area_m2": pick(
            vehicle, ("frontal_area_m2", "frontal_area"), 2.22
        ),
        "drag_coefficient": pick(
            vehicle, ("drag_coefficient", "cd"), 0.23
        ),
        "rolling_coefficient": pick(
            vehicle, ("rolling_resistance_coefficient", "crr"), 0.010
        ),
        "drivetrain_efficiency": pick(
            powertrain, ("drivetrain_efficiency", "traction_efficiency"), 0.92
        ),
        "regen_efficiency": pick(
            powertrain, ("regenerative_efficiency", "regen_efficiency"), 0.80
        ),
        "max_regen_power_w": pick(
            powertrain, ("max_regen_power_w",), float("inf")
        ),
        "regen_cutoff_speed_mps": pick(
            powertrain, ("regen_cutoff_speed_mps",), 1.5
        ),
        "regen_full_speed_mps": pick(
            powertrain, ("regen_full_speed_mps",), 5.0
        ),
        "auxiliary_power_w": aux_w,
    }


def _calculate_energy_breakdown(times, speeds, grades_deg, params):
    m = params["mass_kg"]
    area = params["frontal_area_m2"]
    cd = params["drag_coefficient"]
    crr = params["rolling_coefficient"]
    eta_drive = params["drivetrain_efficiency"]
    eta_regen = params["regen_efficiency"]
    max_regen_w = params["max_regen_power_w"]
    aux_w = max(0.0, params["auxiliary_power_w"])

    if not 0.0 < eta_drive <= 1.0:
        raise ValueError("drivetrain_efficiency must be in (0, 1].")
    if not 0.0 <= eta_regen <= 1.0:
        raise ValueError("regenerative_efficiency must be in [0, 1].")

    aero_j = roll_j = inertial_j = grade_j = 0.0
    positive_wheel_j = negative_wheel_j = 0.0
    battery_traction_j = regen_j = friction_j = auxiliary_j = 0.0

    for i in range(1, len(times)):
        dt = times[i] - times[i - 1]
        if dt <= 0.0:
            continue

        v0 = max(0.0, speeds[i - 1])
        v1 = max(0.0, speeds[i])
        v = 0.5 * (v0 + v1)
        a = (v1 - v0) / dt
        theta = math.radians(0.5 * (grades_deg[i - 1] + grades_deg[i]))

        p_aero = 0.5 * AIR_DENSITY_KG_M3 * cd * area * v**3
        p_roll = m * GRAVITY_MPS2 * crr * math.cos(theta) * v
        p_grade = m * GRAVITY_MPS2 * math.sin(theta) * v
        p_inertial = m * a * v
        p_wheel = p_aero + p_roll + p_grade + p_inertial

        aero_j += p_aero * dt
        roll_j += p_roll * dt
        grade_j += p_grade * dt
        inertial_j += p_inertial * dt

        if p_wheel >= 0.0:
            wheel_j = p_wheel * dt
            positive_wheel_j += wheel_j
            battery_traction_j += wheel_j / eta_drive
        else:
            braking_w = -p_wheel
            negative_wheel_j += braking_w * dt

            split = split_regen_and_friction(
                speed_mps=v,
                braking_wheel_power_w=braking_w,
                regenerative_efficiency=eta_regen,
                max_regen_dc_power_w=max_regen_w,
                cutoff_speed_mps=params["regen_cutoff_speed_mps"],
                full_regen_speed_mps=params["regen_full_speed_mps"],
            )

            regen_j += split.recovered_dc_power_w * dt
            friction_j += split.friction_brake_power_w * dt

        auxiliary_j += aux_w * dt

    drivetrain_loss_j = battery_traction_j - positive_wheel_j
    net_battery_j = battery_traction_j - regen_j + auxiliary_j
    kwh = 1.0 / 3_600_000.0

    return {
        "aerodynamic_energy_kwh": aero_j * kwh,
        "rolling_resistance_energy_kwh": roll_j * kwh,
        "inertial_energy_kwh": inertial_j * kwh,
        "grade_energy_kwh": grade_j * kwh,
        "positive_wheel_traction_energy_kwh": positive_wheel_j * kwh,
        "negative_wheel_energy_kwh": negative_wheel_j * kwh,
        "drivetrain_loss_energy_kwh": drivetrain_loss_j * kwh,
        "recovered_regen_energy_breakdown_kwh": regen_j * kwh,
        "friction_brake_energy_breakdown_kwh": friction_j * kwh,
        "regen_capture_efficiency_percent": (
            100.0 * regen_j / negative_wheel_j
            if negative_wheel_j > 0.0
            else 0.0
        ),
        "auxiliary_energy_kwh": auxiliary_j * kwh,
        "net_battery_energy_breakdown_kwh": net_battery_j * kwh,
    }



GRADE_CLAMP_DEG = 15.0
GRADE_CLAMP_TOLERANCE_DEG = 1e-3
ELEVATION_SMOOTHING_RADIUS_M = 25.0
ELEVATION_GRADE_BASELINE_M = 25.0


def _read_cycle_metadata(path: str | Path) -> dict[str, str]:
    metadata: dict[str, str] = {}

    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()

            if not stripped.startswith("#"):
                break

            text = stripped[1:].strip()
            if "=" not in text:
                continue

            key, value = text.split("=", 1)
            metadata[key.strip()] = value.strip()

    return metadata


def _metadata_float(
    metadata: dict[str, str],
    *keys: str,
) -> float | None:
    for key in keys:
        value = metadata.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _endpoint_dem_elevations(
    cycle_path: str | Path,
    vehicle_config_path: str | Path,
) -> tuple[float | None, float | None]:
    metadata = _read_cycle_metadata(cycle_path)

    start_lat = _metadata_float(
        metadata,
        "start_lat",
        "route_start_lat",
    )
    start_lon = _metadata_float(
        metadata,
        "start_lon",
        "start_lng",
        "route_start_lon",
        "route_start_lng",
    )
    end_lat = _metadata_float(
        metadata,
        "end_lat",
        "route_end_lat",
    )
    end_lon = _metadata_float(
        metadata,
        "end_lon",
        "end_lng",
        "route_end_lon",
        "route_end_lng",
    )

    if (
        start_lat is None
        or start_lon is None
        or end_lat is None
        or end_lon is None
    ):
        return None, None

    config_path = Path(vehicle_config_path).resolve()

    # Expected:
    # sim/src/drive_cycles/car_configs/tesla_model3_lr_rwd.yaml
    # -> sim/cache/elevation/dem
    try:
        sim_root = config_path.parents[3]
    except IndexError:
        return None, None

    dem_dir = sim_root / "cache" / "elevation" / "dem"

    if not dem_dir.is_dir():
        return None, None

    dem = LocalHgtDem(dem_dir)

    try:
        start_elevation = dem.elevation_m(start_lat, start_lon)
        end_elevation = dem.elevation_m(end_lat, end_lon)
    except (FileNotFoundError, ValueError):
        return None, None
    finally:
        dem.close()

    return (
        float(start_elevation) if start_elevation is not None else None,
        float(end_elevation) if end_elevation is not None else None,
    )


def _elevation_grade_statistics(
    times: list[float],
    speeds: list[float],
    grades_deg: list[float],
) -> dict:
    if not grades_deg:
        return {
            "grade_integrated_net_elevation_change_m": 0.0,
            "total_ascent_m": 0.0,
            "total_descent_m": 0.0,
            "max_uphill_grade_deg": 0.0,
            "max_downhill_grade_deg": 0.0,
            "mean_abs_grade_deg": 0.0,
            "distance_weighted_mean_abs_grade_deg": 0.0,
            "grade_clamp_sample_count": 0,
            "grade_clamp_sample_percent": 0.0,
        }

    ascent_m = 0.0
    descent_m = 0.0
    net_delta_m = 0.0
    weighted_abs_grade_sum = 0.0
    distance_sum_m = 0.0

    for i in range(1, len(times)):
        dt = times[i] - times[i - 1]
        if dt <= 0.0:
            continue

        v0 = max(0.0, speeds[i - 1])
        v1 = max(0.0, speeds[i])
        ds = 0.5 * (v0 + v1) * dt

        grade_deg = 0.5 * (grades_deg[i - 1] + grades_deg[i])
        dh = ds * math.tan(math.radians(grade_deg))

        net_delta_m += dh
        if dh >= 0.0:
            ascent_m += dh
        else:
            descent_m += -dh

        weighted_abs_grade_sum += abs(grade_deg) * ds
        distance_sum_m += ds

    clamp_count = sum(
        1
        for grade in grades_deg
        if abs(grade) >= (
            GRADE_CLAMP_DEG
            - GRADE_CLAMP_TOLERANCE_DEG
        )
    )

    clamp_percent = (
        100.0 * clamp_count / len(grades_deg)
        if grades_deg
        else 0.0
    )

    if clamp_count == 0:
        guard_status = "CLEAR"
    elif clamp_percent <= 0.10:
        guard_status = "MINOR_CONTACT"
    elif clamp_percent <= 1.00:
        guard_status = "REVIEW"
    else:
        guard_status = "SIGNIFICANT_CLIPPING"

    return {
        "grade_integrated_net_elevation_change_m": net_delta_m,
        "total_ascent_m": ascent_m,
        "total_descent_m": descent_m,
        "max_uphill_grade_deg": max(grades_deg),
        "max_downhill_grade_deg": min(grades_deg),
        "mean_abs_grade_deg": mean(abs(value) for value in grades_deg),
        "distance_weighted_mean_abs_grade_deg": (
            weighted_abs_grade_sum / distance_sum_m
            if distance_sum_m > 0.0
            else 0.0
        ),
        "grade_clamp_sample_count": clamp_count,
        "grade_clamp_sample_percent": clamp_percent,
        "grade_guard_status": guard_status,
    }


def _assessment(
    error_percent: float,
) -> str:
    magnitude = abs(
        error_percent
    )

    if magnitude <= 10.0:
        return "GOOD_SANITY_MATCH"

    if magnitude <= 20.0:
        return "REVIEW"

    return "LARGE_MISMATCH"


def validate_vehicle_route(
    cycle_path: str | Path,
    vehicle_config_path: str | Path,
) -> VehicleRouteValidation:
    cycle_path = Path(
        cycle_path
    )

    benchmark = _load_validation_benchmark(
        vehicle_config_path
    )

    summary = analyze_route_summary(
        cycle_path,
        vehicle_config_path,
    )

    times, speeds, grades = _load_cycle_samples(
        cycle_path
    )

    elevation_stats = _elevation_grade_statistics(
        times,
        speeds,
        grades,
    )

    start_elevation_m, end_elevation_m = _endpoint_dem_elevations(
        cycle_path,
        vehicle_config_path,
    )

    endpoint_dem_delta_m = (
        end_elevation_m - start_elevation_m
        if start_elevation_m is not None and end_elevation_m is not None
        else None
    )

    elevation_profile_consistency_error_m = (
        elevation_stats["grade_integrated_net_elevation_change_m"]
        - endpoint_dem_delta_m
        if endpoint_dem_delta_m is not None
        else None
    )

    energy_params = _vehicle_energy_parameters(
        vehicle_config_path
    )
    breakdown = _calculate_energy_breakdown(
        times,
        speeds,
        grades,
        energy_params,
    )

    accelerations = _acceleration_samples(
        times,
        speeds,
    )

    duration_s = (
        times[-1]
        - times[0]
    )

    distance_km = float(
        summary.distance_km
    )

    average_speed_kmh = (
        distance_km
        / (duration_s / 3600.0)
        if duration_s > 0.0
        else 0.0
    )

    peak_speed_kmh = (
        max(speeds)
        * 3.6
    )

    stopped_dt = 0.0
    total_dt = 0.0

    for index in range(
        1,
        len(times),
    ):
        dt = (
            times[index]
            - times[index - 1]
        )

        total_dt += dt

        average_interval_speed = (
            0.5
            * (
                speeds[index]
                + speeds[index - 1]
            )
        )

        if average_interval_speed < (
            1.0 / 3.6
        ):
            stopped_dt += dt

    stopped_time_percent = (
        100.0
        * stopped_dt
        / total_dt
        if total_dt > 0.0
        else 0.0
    )

    positive_accel = [
        value
        for value in accelerations
        if value > 0.05
    ]

    braking_magnitudes = [
        -value
        for value in accelerations
        if value < -0.05
    ]

    net_dc_energy_kwh = float(
        summary.net_dc_energy_kwh
    )

    simulated_wh_per_km = (
        net_dc_energy_kwh
        * 1000.0
        / distance_km
        if distance_km > 0.0
        else math.nan
    )

    benchmark_wh_per_km = float(
        benchmark["wh_per_km"]
    )

    energy_error_wh_per_km = (
        simulated_wh_per_km
        - benchmark_wh_per_km
    )

    energy_error_percent = (
        100.0
        * energy_error_wh_per_km
        / benchmark_wh_per_km
    )

    traction_energy = float(
        summary.traction_energy_kwh
    )

    recovered_energy = float(
        summary.recovered_energy_kwh
    )

    recovered_fraction = (
        100.0
        * recovered_energy
        / traction_energy
        if traction_energy > 0.0
        else 0.0
    )

    return VehicleRouteValidation(
        profile_name=cycle_path.stem,
        source_cycle=str(
            cycle_path.resolve()
        ),
        vehicle_name=str(
            summary.vehicle_name
        ),
        benchmark_name=str(
            benchmark["name"]
        ),
        benchmark_cycle=str(
            benchmark["cycle"]
        ),
        benchmark_wh_per_km=benchmark_wh_per_km,
        duration_s=duration_s,
        distance_km=distance_km,
        average_speed_kmh=average_speed_kmh,
        peak_speed_kmh=peak_speed_kmh,
        stopped_time_percent=stopped_time_percent,
        mean_positive_accel_mps2=(
            mean(positive_accel)
            if positive_accel
            else 0.0
        ),
        p95_positive_accel_mps2=_percentile(
            positive_accel,
            0.95,
        ),
        mean_braking_mps2=(
            mean(braking_magnitudes)
            if braking_magnitudes
            else 0.0
        ),
        p95_braking_magnitude_mps2=_percentile(
            braking_magnitudes,
            0.95,
        ),
        traction_energy_kwh=traction_energy,
        recovered_energy_kwh=recovered_energy,
        net_dc_energy_kwh=net_dc_energy_kwh,
        simulated_wh_per_km=simulated_wh_per_km,
        energy_error_wh_per_km=energy_error_wh_per_km,
        energy_error_percent=energy_error_percent,
        recovered_fraction_percent=recovered_fraction,
        aerodynamic_energy_kwh=breakdown["aerodynamic_energy_kwh"],
        rolling_resistance_energy_kwh=breakdown["rolling_resistance_energy_kwh"],
        inertial_energy_kwh=breakdown["inertial_energy_kwh"],
        grade_energy_kwh=breakdown["grade_energy_kwh"],
        positive_wheel_traction_energy_kwh=breakdown[
            "positive_wheel_traction_energy_kwh"
        ],
        negative_wheel_energy_kwh=breakdown["negative_wheel_energy_kwh"],
        drivetrain_loss_energy_kwh=breakdown["drivetrain_loss_energy_kwh"],
        recovered_regen_energy_breakdown_kwh=breakdown[
            "recovered_regen_energy_breakdown_kwh"
        ],
        friction_brake_energy_breakdown_kwh=breakdown[
            "friction_brake_energy_breakdown_kwh"
        ],
        regen_capture_efficiency_percent=breakdown[
            "regen_capture_efficiency_percent"
        ],
        auxiliary_energy_kwh=breakdown["auxiliary_energy_kwh"],
        net_battery_energy_breakdown_kwh=breakdown[
            "net_battery_energy_breakdown_kwh"
        ],
        breakdown_wh_per_km=(
            breakdown["net_battery_energy_breakdown_kwh"] * 1000.0 / distance_km
            if distance_km > 0.0 else math.nan
        ),
        start_elevation_m=start_elevation_m,
        end_elevation_m=end_elevation_m,
        endpoint_dem_delta_m=endpoint_dem_delta_m,
        grade_integrated_net_elevation_change_m=elevation_stats[
            "grade_integrated_net_elevation_change_m"
        ],
        total_ascent_m=elevation_stats["total_ascent_m"],
        total_descent_m=elevation_stats["total_descent_m"],
        max_uphill_grade_deg=elevation_stats["max_uphill_grade_deg"],
        max_downhill_grade_deg=elevation_stats["max_downhill_grade_deg"],
        mean_abs_grade_deg=elevation_stats["mean_abs_grade_deg"],
        distance_weighted_mean_abs_grade_deg=elevation_stats[
            "distance_weighted_mean_abs_grade_deg"
        ],
        grade_clamp_sample_count=elevation_stats[
            "grade_clamp_sample_count"
        ],
        grade_clamp_sample_percent=elevation_stats[
            "grade_clamp_sample_percent"
        ],
        grade_guard_deg=GRADE_CLAMP_DEG,
        elevation_smoothing_radius_m=ELEVATION_SMOOTHING_RADIUS_M,
        elevation_grade_baseline_m=ELEVATION_GRADE_BASELINE_M,
        grade_guard_status=elevation_stats[
            "grade_guard_status"
        ],
        elevation_profile_consistency_error_m=(
            elevation_profile_consistency_error_m
        ),
        wltc_average_speed_delta_kmh=(
            average_speed_kmh
            - WLTC_CLASS3_AVERAGE_SPEED_KMH
        ),
        wltc_peak_speed_delta_kmh=(
            peak_speed_kmh
            - WLTC_CLASS3_MAX_SPEED_KMH
        ),
        wltc_stopped_time_delta_percent=(
            stopped_time_percent
            - WLTC_CLASS3_STOPPED_TIME_PERCENT
        ),
        energy_assessment=_assessment(
            energy_error_percent
        ),
        comparison_scope=(
            "Route sanity comparison only. "
            "A Stuttgart traffic mission is not a WLTP certification test."
        ),
    )


def save_vehicle_route_validation(
    result: VehicleRouteValidation,
    output_base: str | Path | None = None,
) -> tuple[Path, Path]:
    source = Path(
        result.source_cycle
    )

    if output_base is None:
        output_base = (
            source.parent
            / f"{source.stem}_vehicle_validation"
        )

    output_base = Path(
        output_base
    )

    csv_path = output_base.with_suffix(
        ".csv"
    )

    json_path = output_base.with_suffix(
        ".json"
    )

    data = asdict(
        result
    )

    with csv_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(
                data.keys()
            ),
        )

        writer.writeheader()
        writer.writerow(
            data
        )

    with json_path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            data,
            handle,
            indent=2,
        )

    return (
        csv_path,
        json_path,
    )
