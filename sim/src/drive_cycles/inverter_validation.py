from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from drive_cycles.inverter_electrical import (
    load_inverter_config,
    run_inverter_analysis,
    validate_datasheet_reference_point,
)


@dataclass(frozen=True)
class DatasheetCheck:
    name: str
    model_value: float
    datasheet_value: float
    unit: str
    error_percent: float
    passed: bool


@dataclass(frozen=True)
class InverterDatasheetValidation:
    device_name: str
    reference_current_a: float
    reference_voltage_v: float
    checks: tuple[DatasheetCheck, ...]
    overall_pass: bool


@dataclass(frozen=True)
class InverterMissionValidation:
    source_cycle: str
    peak_phase_current_a: float
    peak_device_current_a: float
    peak_total_loss_w: float
    peak_unserved_power_w: float
    loss_energy_wh: float
    conduction_energy_wh: float
    switching_energy_wh: float
    switching_fraction_percent: float
    served_without_current_limit: bool


# Wolfspeed CAB525F12XM3 published reference values used by the current model.
# Electrical reference conditions:
#   VDD = 600 V
#   ID  = 450 A
# RDS(on):
#   25 C  = 2.6 mOhm typ
#   175 C = 4.7 mOhm typ
# Switching energy:
#   25 C:  Eon 25.4 mJ, Eoff 7.5 mJ
#   125 C: Eon 24.0 mJ, Eoff 8.1 mJ
#   175 C: Eon 24.4 mJ, Eoff 8.4 mJ
DATASHEET_REFERENCE = {
    "rds_25c_ohm": (0.0026, "ohm"),
    "rds_175c_ohm": (0.0047, "ohm"),
    "eon_25c_j": (0.0254, "J"),
    "eoff_25c_j": (0.0075, "J"),
    "eon_125c_j": (0.0240, "J"),
    "eoff_125c_j": (0.0081, "J"),
    "eon_175c_j": (0.0244, "J"),
    "eoff_175c_j": (0.0084, "J"),
}


DISPLAY_NAMES = {
    "rds_25c_ohm": "RDS(on) @ 25 C",
    "rds_175c_ohm": "RDS(on) @ 175 C",
    "eon_25c_j": "Eon @ 25 C",
    "eoff_25c_j": "Eoff @ 25 C",
    "eon_125c_j": "Eon @ 125 C",
    "eoff_125c_j": "Eoff @ 125 C",
    "eon_175c_j": "Eon @ 175 C",
    "eoff_175c_j": "Eoff @ 175 C",
}


def validate_cab525f12xm3_datasheet(
    vehicle_config_path: str | Path,
    *,
    tolerance_percent: float = 1.0,
) -> InverterDatasheetValidation:
    config = load_inverter_config(
        vehicle_config_path
    )

    model = validate_datasheet_reference_point(
        config
    )

    checks = []

    for key, (
        datasheet_value,
        unit,
    ) in DATASHEET_REFERENCE.items():
        model_value = float(
            model[key]
        )

        error_percent = (
            100.0
            * (model_value - datasheet_value)
            / datasheet_value
        )

        checks.append(
            DatasheetCheck(
                name=DISPLAY_NAMES[key],
                model_value=model_value,
                datasheet_value=datasheet_value,
                unit=unit,
                error_percent=error_percent,
                passed=(
                    abs(error_percent)
                    <= tolerance_percent
                ),
            )
        )

    current_error_percent = (
        100.0
        * (
            float(
                model[
                    "switching_reference_current_a"
                ]
            )
            - 450.0
        )
        / 450.0
    )

    voltage_error_percent = (
        100.0
        * (
            float(
                model[
                    "switching_reference_voltage_v"
                ]
            )
            - 600.0
        )
        / 600.0
    )

    checks.extend(
        [
            DatasheetCheck(
                name="Switching reference current",
                model_value=float(
                    model[
                        "switching_reference_current_a"
                    ]
                ),
                datasheet_value=450.0,
                unit="A",
                error_percent=current_error_percent,
                passed=(
                    abs(current_error_percent)
                    <= tolerance_percent
                ),
            ),
            DatasheetCheck(
                name="Switching reference voltage",
                model_value=float(
                    model[
                        "switching_reference_voltage_v"
                    ]
                ),
                datasheet_value=600.0,
                unit="V",
                error_percent=voltage_error_percent,
                passed=(
                    abs(voltage_error_percent)
                    <= tolerance_percent
                ),
            ),
        ]
    )

    return InverterDatasheetValidation(
        device_name="Wolfspeed CAB525F12XM3",
        reference_current_a=450.0,
        reference_voltage_v=600.0,
        checks=tuple(checks),
        overall_pass=all(
            check.passed
            for check in checks
        ),
    )


def run_inverter_mission_validation(
    drive_cycle_path: str | Path,
    vehicle_config_path: str | Path,
) -> InverterMissionValidation:
    result, _ = run_inverter_analysis(
        drive_cycle_path,
        vehicle_config_path,
    )

    samples = result.samples

    conduction_energy_j = 0.0
    switching_energy_j = 0.0

    previous_time = None

    for sample in samples:
        if previous_time is not None:
            dt = (
                sample.time_s
                - previous_time
            )
            if dt > 0.0:
                conduction_energy_j += (
                    sample.conduction_loss_w
                    * dt
                )
                switching_energy_j += (
                    sample.switching_loss_w
                    * dt
                )

        previous_time = sample.time_s

    conduction_energy_wh = (
        conduction_energy_j
        / 3600.0
    )
    switching_energy_wh = (
        switching_energy_j
        / 3600.0
    )

    total = (
        conduction_energy_wh
        + switching_energy_wh
    )

    switching_fraction_percent = (
        100.0
        * switching_energy_wh
        / total
        if total > 0.0
        else 0.0
    )

    return InverterMissionValidation(
        source_cycle=str(
            Path(
                drive_cycle_path
            ).resolve()
        ),
        peak_phase_current_a=(
            result.peak_phase_current_a
        ),
        peak_device_current_a=(
            result.peak_device_current_a
        ),
        peak_total_loss_w=(
            result.peak_total_loss_w
        ),
        peak_unserved_power_w=(
            result.peak_unserved_power_w
        ),
        loss_energy_wh=(
            result.loss_energy_wh
        ),
        conduction_energy_wh=(
            conduction_energy_wh
        ),
        switching_energy_wh=(
            switching_energy_wh
        ),
        switching_fraction_percent=(
            switching_fraction_percent
        ),
        served_without_current_limit=(
            result.peak_unserved_power_w
            <= 1.0
        ),
    )
