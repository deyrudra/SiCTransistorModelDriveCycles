from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import csv
import math
from pathlib import Path
from statistics import mean

from drive_cycles.route_summary import analyze_route_summary


DERIVED_SUFFIXES = (
    "_power",
    "_vehicle_power",
    "_inverter_losses",
    "_thermal_trace",
    "_rainflow",
    "_relative_damage",
    "_summary",
    "_comparison",
)


def is_raw_drive_cycle_csv(path: str | Path) -> bool:
    path = Path(path)

    if path.suffix.lower() != ".csv":
        return False

    if any(
        path.stem.endswith(suffix)
        for suffix in DERIVED_SUFFIXES
    ):
        return False

    try:
        with path.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as handle:
            for line in handle:
                stripped = line.strip()

                if (
                    not stripped
                    or stripped.startswith("#")
                ):
                    continue

                return [
                    item.strip()
                    for item in stripped.split(",")
                ] == [
                    "time_s",
                    "v_mps",
                    "grade_deg",
                ]
    except OSError:
        return False

    return False


def discover_profiles(
    cycles_dir: str | Path,
) -> list[dict]:
    root = Path(cycles_dir)

    if not root.is_dir():
        return []

    rows = []

    for path in root.glob("*.csv"):
        if not is_raw_drive_cycle_csv(path):
            continue

        try:
            stat = path.stat()
        except OSError:
            continue

        rows.append(
            {
                "name": path.stem,
                "path": str(path.resolve()),
                "modified": stat.st_mtime,
            }
        )

    rows.sort(
        key=lambda row: row["modified"],
        reverse=True,
    )

    return rows


def read_cycle_shape_metrics(
    path: str | Path,
) -> dict:
    path = Path(path)

    times = []
    speeds = []
    grades_deg = []

    with path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        lines = (
            line
            for line in handle
            if not line.lstrip().startswith("#")
        )

        reader = csv.DictReader(lines)

        for row in reader:
            times.append(
                float(row["time_s"])
            )
            speeds.append(
                float(row["v_mps"])
            )
            grades_deg.append(
                float(row["grade_deg"])
            )

    if len(times) < 2:
        raise ValueError(
            f"Profile has fewer than two samples: {path.name}"
        )

    distance_m = 0.0
    ascent_m = 0.0
    descent_m = 0.0
    stopped_s = 0.0
    stop_events = 0
    accel_energy_proxy = 0.0

    was_stopped = (
        speeds[0] < 0.5
    )

    for index in range(
        1,
        len(times),
    ):
        dt = (
            times[index]
            - times[index - 1]
        )

        if dt <= 0.0:
            continue

        v0 = speeds[index - 1]
        v1 = speeds[index]
        v_avg = 0.5 * (
            v0 + v1
        )

        ds = max(
            0.0,
            v_avg * dt,
        )

        distance_m += ds

        grade_rad = math.radians(
            0.5
            * (
                grades_deg[index - 1]
                + grades_deg[index]
            )
        )

        dz = math.tan(
            grade_rad
        ) * ds

        if dz >= 0.0:
            ascent_m += dz
        else:
            descent_m += -dz

        is_stopped = (
            v_avg < 0.5
        )

        if is_stopped:
            stopped_s += dt

        current_stopped = (
            v1 < 0.5
        )

        if (
            current_stopped
            and not was_stopped
        ):
            stop_events += 1

        was_stopped = (
            current_stopped
        )

        if v1 > v0:
            accel_energy_proxy += (
                v1 * v1
                - v0 * v0
            )

    duration_s = (
        times[-1]
        - times[0]
    )

    distance_km = (
        distance_m
        / 1000.0
    )

    avg_speed_kmh = (
        distance_km
        / (
            duration_s
            / 3600.0
        )
        if duration_s > 0.0
        else 0.0
    )

    stopped_percent = (
        100.0
        * stopped_s
        / duration_s
        if duration_s > 0.0
        else 0.0
    )

    return {
        "duration_s_raw": duration_s,
        "distance_km_raw": distance_km,
        "average_speed_kmh": avg_speed_kmh,
        "peak_speed_kmh": (
            max(speeds)
            * 3.6
        ),
        "stopped_time_percent": stopped_percent,
        "stop_events": stop_events,
        "ascent_m": ascent_m,
        "descent_m": descent_m,
        "net_elevation_change_m": (
            ascent_m
            - descent_m
        ),
        "speed_change_proxy": accel_energy_proxy,
    }


def analyze_profile(
    cycle_path: str | Path,
    vehicle_config_path: str | Path,
) -> dict:
    summary = analyze_route_summary(
        cycle_path,
        vehicle_config_path,
    )

    row = asdict(
        summary
    )

    row["source_cycle"] = str(
        Path(cycle_path).resolve()
    )
    row["profile_name"] = (
        Path(cycle_path).stem
    )

    row.update(
        read_cycle_shape_metrics(
            cycle_path
        )
    )

    distance = max(
        float(
            row["distance_km"]
        ),
        1e-12,
    )

    row["wh_per_km"] = (
        float(
            row["net_dc_energy_kwh"]
        )
        * 1000.0
        / distance
    )

    return row


def analyze_group(
    cycle_paths,
    vehicle_config_path,
) -> list[dict]:
    return [
        analyze_profile(
            path,
            vehicle_config_path,
        )
        for path in cycle_paths
    ]


def group_mean(
    rows: list[dict],
) -> dict:
    if not rows:
        return {}

    keys = (
        "duration_s",
        "distance_km",
        "average_speed_kmh",
        "peak_speed_kmh",
        "stopped_time_percent",
        "stop_events",
        "ascent_m",
        "descent_m",
        "net_dc_energy_kwh",
        "wh_per_km",
        "peak_phase_current_a",
        "peak_aggregate_semiconductor_loss_w",
        "peak_junction_temperature_c",
        "maximum_delta_tj_c",
        "equivalent_full_cycles",
        "total_relative_damage",
    )

    return {
        key: mean(
            float(
                row[key]
            )
            for row in rows
        )
        for key in keys
    }


def normalized_balanced_ranking(
    rows: list[dict],
    *,
    time_weight: float = 1.0,
    energy_weight: float = 1.0,
    damage_weight: float = 1.0,
) -> list[dict]:
    if not rows:
        return []

    metric_specs = (
        (
            "duration_s",
            time_weight,
        ),
        (
            "net_dc_energy_kwh",
            energy_weight,
        ),
        (
            "total_relative_damage",
            damage_weight,
        ),
    )

    extrema = {}

    for key, _ in metric_specs:
        values = [
            float(
                row[key]
            )
            for row in rows
        ]

        extrema[key] = (
            min(values),
            max(values),
        )

    ranked = []

    for row in rows:
        score = 0.0
        weight_total = 0.0

        for key, weight in metric_specs:
            if weight <= 0.0:
                continue

            low, high = (
                extrema[key]
            )

            if abs(
                high - low
            ) <= 1e-15:
                normalized = 0.0
            else:
                normalized = (
                    float(
                        row[key]
                    )
                    - low
                ) / (
                    high
                    - low
                )

            score += (
                weight
                * normalized
            )
            weight_total += (
                weight
            )

        output = dict(
            row
        )
        output["balanced_score"] = (
            score
            / weight_total
            if weight_total > 0.0
            else 0.0
        )

        ranked.append(
            output
        )

    ranked.sort(
        key=lambda row: (
            row["balanced_score"],
            row["duration_s"],
        )
    )

    for index, row in enumerate(
        ranked,
        start=1,
    ):
        row["balanced_rank"] = (
            index
        )

    return ranked


def export_experiment_csv(
    *,
    experiment_name: str,
    rows: list[dict],
    output_dir: str | Path,
) -> Path:
    root = Path(
        output_dir
    )

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = (
        datetime.now()
        .strftime(
            "%Y%m%d_%H%M%S"
        )
    )

    safe_name = "".join(
        character
        if (
            character.isalnum()
            or character in (
                "-",
                "_",
            )
        )
        else "_"
        for character
        in experiment_name.lower()
    )

    path = (
        root
        / (
            f"{safe_name}_"
            f"{timestamp}.csv"
        )
    )

    if not rows:
        raise ValueError(
            "There are no experiment results to export."
        )

    preferred = [
        "experiment_group",
        "balanced_rank",
        "profile_name",
        "source_cycle",
        "duration_s",
        "distance_km",
        "average_speed_kmh",
        "peak_speed_kmh",
        "stopped_time_percent",
        "stop_events",
        "ascent_m",
        "descent_m",
        "net_elevation_change_m",
        "traction_energy_kwh",
        "recovered_energy_kwh",
        "total_auxiliary_energy_kwh",
        "net_dc_energy_kwh",
        "wh_per_km",
        "peak_phase_current_a",
        "peak_aggregate_semiconductor_loss_w",
        "peak_junction_temperature_c",
        "maximum_delta_tj_c",
        "equivalent_full_cycles",
        "total_relative_damage",
        "balanced_score",
    ]

    fields = [
        key
        for key in preferred
        if any(
            key in row
            for row in rows
        )
    ]

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(
                row
            )

    return path
