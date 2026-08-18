from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from drive_cycles.thermal_model import (
    load_thermal_config,
    run_thermal_analysis,
    transient_zth_c_per_w,
)


@dataclass(frozen=True)
class ThermalReferenceCheck:
    time_s: float
    datasheet_zth_c_per_w: float
    model_zth_c_per_w: float
    error_percent: float
    passed: bool


@dataclass(frozen=True)
class ThermalDatasheetValidation:
    device_name: str
    fluid_temperature_c: float
    flow_rate_lpm: float
    datasheet_steady_rth_c_per_w: float
    model_steady_rth_c_per_w: float
    steady_error_percent: float
    steady_pass: bool
    transient_checks: tuple[ThermalReferenceCheck, ...]
    overall_pass: bool


@dataclass(frozen=True)
class ThermalMissionValidation:
    source_cycle: str
    peak_phase_current_a: float
    peak_aggregate_loss_w: float
    peak_device_loss_w: float
    fluid_temperature_c: float
    peak_junction_temperature_c: float
    peak_delta_tj_c: float
    total_loss_energy_wh: float
    nonconverged_samples: int
    overtemperature_samples: int
    solver_pass: bool
    temperature_pass: bool


# Approximate single-pulse points digitized/read from Wolfspeed CAB525F12XM3
# Rev. 3 Figure 17. They are validation anchors for the fitted Foster curve;
# the exact hard datasheet scalar is Rth,J-F = 0.145 C/W at 4 LPM.
FIG17_APPROX_POINTS = (
    (1.0e-6, 0.0005),
    (1.0e-5, 0.0020),
    (1.0e-4, 0.0070),
    (1.0e-3, 0.0250),
    (1.0e-2, 0.0650),
    (1.0e-1, 0.1150),
    (1.0, 0.1400),
    (10.0, 0.1450),
)


def validate_cab525f12xm3_thermal(
    vehicle_config_path: str | Path,
    *,
    transient_tolerance_percent: float = 15.0,
    steady_tolerance_percent: float = 2.0,
) -> ThermalDatasheetValidation:
    config = load_thermal_config(
        vehicle_config_path
    )

    target_steady = 0.145
    model_steady = sum(
        pair.rth_c_per_w
        for pair in config.foster_pairs
    )

    steady_error = (
        100.0
        * (
            model_steady
            - target_steady
        )
        / target_steady
    )

    transient_checks = []

    for time_s, target_zth in FIG17_APPROX_POINTS:
        model_zth = transient_zth_c_per_w(
            config,
            time_s,
        )

        error = (
            100.0
            * (
                model_zth
                - target_zth
            )
            / target_zth
        )

        transient_checks.append(
            ThermalReferenceCheck(
                time_s=time_s,
                datasheet_zth_c_per_w=target_zth,
                model_zth_c_per_w=model_zth,
                error_percent=error,
                passed=(
                    abs(error)
                    <= transient_tolerance_percent
                ),
            )
        )

    steady_pass = (
        abs(steady_error)
        <= steady_tolerance_percent
    )

    overall_pass = (
        steady_pass
        and all(
            item.passed
            for item in transient_checks
        )
    )

    return ThermalDatasheetValidation(
        device_name="Wolfspeed CAB525F12XM3",
        fluid_temperature_c=config.fluid_temperature_c,
        flow_rate_lpm=4.0,
        datasheet_steady_rth_c_per_w=target_steady,
        model_steady_rth_c_per_w=model_steady,
        steady_error_percent=steady_error,
        steady_pass=steady_pass,
        transient_checks=tuple(
            transient_checks
        ),
        overall_pass=overall_pass,
    )


def run_thermal_mission_validation(
    drive_cycle_path: str | Path,
    vehicle_config_path: str | Path,
) -> ThermalMissionValidation:
    result, _ = run_thermal_analysis(
        drive_cycle_path,
        vehicle_config_path,
    )

    fluid = (
        result.peak_fluid_temperature_c
    )

    peak_tj = (
        result.peak_junction_temperature_c
    )

    return ThermalMissionValidation(
        source_cycle=str(
            Path(
                drive_cycle_path
            ).resolve()
        ),
        peak_phase_current_a=(
            result.peak_phase_current_a
        ),
        peak_aggregate_loss_w=(
            result.peak_aggregate_loss_w
        ),
        peak_device_loss_w=(
            result.peak_device_loss_w
        ),
        fluid_temperature_c=fluid,
        peak_junction_temperature_c=peak_tj,
        peak_delta_tj_c=(
            peak_tj - fluid
        ),
        total_loss_energy_wh=(
            result.aggregate_loss_energy_j
            / 3600.0
        ),
        nonconverged_samples=(
            result.nonconverged_samples
        ),
        overtemperature_samples=(
            result.overtemperature_samples
        ),
        solver_pass=(
            result.nonconverged_samples
            == 0
        ),
        temperature_pass=(
            result.overtemperature_samples
            == 0
        ),
    )
