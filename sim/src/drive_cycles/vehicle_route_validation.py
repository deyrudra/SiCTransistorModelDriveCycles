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
]:
    times = []
    speeds = []

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

    if len(times) < 2:
        raise ValueError(
            "Mission profile needs at least two samples."
        )

    return times, speeds


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

    times, speeds = _load_cycle_samples(
        cycle_path
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
