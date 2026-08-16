from __future__ import annotations

"""
Single-route end-to-end summary.

Runs:
    drive-cycle validation
      -> mission profile
      -> longitudinal model
      -> inverter electro-thermal model
      -> rainflow counting
      -> relative reliability damage

and condenses the result into one compact RouteSummary object suitable for
multi-route comparison.
"""

from dataclasses import dataclass, asdict
import csv
import json
from pathlib import Path

from drive_cycles.drive_cycle_loader import (
    build_mission_profile,
    load_drive_cycle,
)
from drive_cycles.vehicle_config import (
    load_vehicle_config,
)
from drive_cycles.longitudinal_profile import (
    analyze_longitudinal_profile,
)
from drive_cycles.inverter_electrical import (
    load_inverter_config,
)
from drive_cycles.thermal_model import (
    analyze_thermal_profile,
    load_thermal_config,
)
from drive_cycles.rainflow_analysis import (
    analyze_temperature_history,
)
from drive_cycles.reliability_damage import (
    analyze_reliability,
    load_reliability_config,
)


@dataclass(frozen=True)
class RouteSummary:
    source_cycle: Path
    vehicle_name: str

    duration_s: float
    distance_km: float

    traction_energy_kwh: float
    recovered_energy_kwh: float
    net_dc_energy_kwh: float
    friction_brake_energy_kwh: float

    peak_wheel_power_kw: float
    peak_dc_propulsion_power_kw: float
    peak_dc_regen_power_kw: float

    peak_phase_current_a: float
    peak_aggregate_semiconductor_loss_w: float
    peak_representative_device_loss_w: float

    peak_case_temperature_c: float
    peak_junction_temperature_c: float
    minimum_junction_temperature_c: float

    rainflow_cycle_count: int
    equivalent_full_cycles: float
    maximum_delta_tj_c: float

    total_relative_damage: float
    reliability_calibrated: bool

    nonconverged_thermal_samples: int
    overtemperature_samples: int


def analyze_route_summary(
    drive_cycle_path: str | Path,
    vehicle_config_path: str | Path,
) -> RouteSummary:
    cycle = load_drive_cycle(
        drive_cycle_path
    )

    profile = build_mission_profile(
        cycle
    )

    vehicle_config = load_vehicle_config(
        vehicle_config_path
    )

    longitudinal = analyze_longitudinal_profile(
        profile,
        vehicle_config,
    )

    inverter_config = load_inverter_config(
        vehicle_config_path
    )

    thermal_config = load_thermal_config(
        vehicle_config_path
    )

    thermal = analyze_thermal_profile(
        longitudinal,
        inverter_config,
        thermal_config,
    )

    temperatures = [
        sample.junction_temperature_c
        for sample in thermal.samples
    ]

    rainflow = analyze_temperature_history(
        temperatures,
        source_cycle=thermal.source_cycle,
    )

    reliability_config = load_reliability_config(
        vehicle_config_path
    )

    reliability = analyze_reliability(
        rainflow,
        reliability_config,
    )

    duration_s = (
        profile.time_s[-1]
        - profile.time_s[0]
    )

    net_energy_kwh = (
        longitudinal.traction_energy_kwh
        - longitudinal.recovered_energy_kwh
    )

    return RouteSummary(
        source_cycle=Path(
            drive_cycle_path
        ),
        vehicle_name=vehicle_config.name,

        duration_s=duration_s,
        distance_km=longitudinal.distance_km,

        traction_energy_kwh=longitudinal.traction_energy_kwh,
        recovered_energy_kwh=longitudinal.recovered_energy_kwh,
        net_dc_energy_kwh=net_energy_kwh,
        friction_brake_energy_kwh=longitudinal.friction_brake_energy_kwh,

        peak_wheel_power_kw=longitudinal.peak_wheel_power_kw,
        peak_dc_propulsion_power_kw=longitudinal.peak_dc_power_kw,
        peak_dc_regen_power_kw=longitudinal.peak_regen_power_kw,

        peak_phase_current_a=thermal.peak_phase_current_a,
        peak_aggregate_semiconductor_loss_w=thermal.peak_aggregate_loss_w,
        peak_representative_device_loss_w=thermal.peak_device_loss_w,

        peak_case_temperature_c=thermal.peak_case_temperature_c,
        peak_junction_temperature_c=thermal.peak_junction_temperature_c,
        minimum_junction_temperature_c=min(
            temperatures
        ),

        rainflow_cycle_count=len(
            rainflow.cycles
        ),
        equivalent_full_cycles=rainflow.equivalent_full_cycles,
        maximum_delta_tj_c=rainflow.maximum_delta_tj_c,

        total_relative_damage=reliability.total_relative_damage,
        reliability_calibrated=reliability.calibrated,

        nonconverged_thermal_samples=thermal.nonconverged_samples,
        overtemperature_samples=thermal.overtemperature_samples,
    )


def write_route_summary_csv(
    summary: RouteSummary,
    output_path: str | Path,
) -> Path:
    path = Path(
        output_path
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = asdict(
        summary
    )

    data["source_cycle"] = str(
        summary.source_cycle
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.writer(
            handle
        )

        writer.writerow(
            [
                "metric",
                "value",
            ]
        )

        for key, value in data.items():
            writer.writerow(
                [
                    key,
                    value,
                ]
            )

    return path


def write_route_summary_json(
    summary: RouteSummary,
    output_path: str | Path,
) -> Path:
    path = Path(
        output_path
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = asdict(
        summary
    )

    data["source_cycle"] = str(
        summary.source_cycle
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            data,
            handle,
            indent=2,
        )

    return path


def run_route_summary(
    drive_cycle_path: str | Path,
    vehicle_config_path: str | Path,
    *,
    csv_output_path: str | Path | None = None,
    json_output_path: str | Path | None = None,
) -> tuple[RouteSummary, Path, Path]:
    summary = analyze_route_summary(
        drive_cycle_path,
        vehicle_config_path,
    )

    source = Path(
        drive_cycle_path
    )

    if csv_output_path is None:
        csv_output_path = source.with_name(
            source.stem
            + "_summary.csv"
        )

    if json_output_path is None:
        json_output_path = source.with_name(
            source.stem
            + "_summary.json"
        )

    csv_path = write_route_summary_csv(
        summary,
        csv_output_path,
    )

    json_path = write_route_summary_json(
        summary,
        json_output_path,
    )

    return (
        summary,
        csv_path,
        json_path,
    )
