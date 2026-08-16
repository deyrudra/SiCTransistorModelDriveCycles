from __future__ import annotations

"""
Offline inverter electrical-loss model for the selected ego/test vehicle.

Pipeline:
    raw drive cycle
      -> validated mission profile
      -> longitudinal DC power demand
      -> inverter current / semiconductor loss

This stage does not yet solve junction temperature dynamically. It evaluates
Rds(on) at a configurable reference junction temperature (default 25 C).
The coupled thermal stage will replace that fixed-temperature assumption.
"""

from dataclasses import dataclass
import csv
import math
from pathlib import Path
from typing import Any

import yaml

from drive_cycles.drive_cycle_loader import (
    build_mission_profile,
    load_drive_cycle,
)
from drive_cycles.longitudinal_profile import (
    analyze_longitudinal_profile,
)
from drive_cycles.vehicle_config import (
    load_vehicle_config,
)


SQRT_3 = math.sqrt(3.0)
SQRT_2 = math.sqrt(2.0)
SWITCH_POSITIONS = 6


@dataclass(frozen=True)
class InverterConfig:
    dc_bus_voltage_v: float
    switching_frequency_hz: float
    max_phase_current_a_peak: float
    parallel_devices_per_switch: int
    modulation_factor: float

    rds_on_ohm_at_25c: float
    rds_on_temperature_exponent: float

    eon_reference_j: float
    eoff_reference_j: float
    switching_reference_current_a: float
    switching_reference_voltage_v: float

    tj_reference_c: float
    tj_max_c: float


@dataclass(frozen=True)
class InverterSample:
    time_s: float
    dc_power_requested_w: float
    dc_power_served_w: float
    unserved_power_w: float

    phase_current_rms_a: float
    phase_current_peak_a: float

    device_current_rms_a: float
    device_current_peak_a: float

    junction_temperature_c: float
    rds_on_device_ohm: float

    conduction_loss_w: float
    switching_loss_w: float
    total_loss_w: float


@dataclass(frozen=True)
class InverterResult:
    samples: tuple[InverterSample, ...]
    source_cycle: Path
    vehicle_name: str
    peak_phase_current_a: float
    peak_device_current_a: float
    peak_total_loss_w: float
    peak_unserved_power_w: float
    loss_energy_wh: float


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML section {name!r} must be a mapping.")
    return value


def _number(
    section: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float | None = None,
) -> float:
    raw = section.get(key, default)

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Configuration value {key!r} must be numeric, got {raw!r}."
        ) from exc

    if minimum is not None and value < minimum:
        raise ValueError(
            f"{key!r} must be >= {minimum}, got {value}."
        )

    return value


def load_inverter_config(
    path: str | Path,
) -> InverterConfig:
    config_path = Path(path).expanduser().resolve()

    if not config_path.is_file():
        raise FileNotFoundError(
            f"Vehicle config not found: {config_path}"
        )

    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError("Vehicle YAML root must be a mapping.")

    inverter = _section(data, "inverter")
    mosfet = _section(data, "mosfet")

    parallel_raw = inverter.get(
        "parallel_devices_per_switch",
        4,
    )

    try:
        parallel = int(parallel_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "parallel_devices_per_switch must be an integer."
        ) from exc

    if parallel < 1:
        raise ValueError(
            "parallel_devices_per_switch must be >= 1."
        )

    return InverterConfig(
        dc_bus_voltage_v=_number(
            inverter,
            "dc_bus_voltage_v",
            345.5,
            minimum=1.0,
        ),
        switching_frequency_hz=_number(
            inverter,
            "switching_frequency_hz",
            10000.0,
            minimum=1.0,
        ),
        max_phase_current_a_peak=_number(
            inverter,
            "max_phase_current_a_peak",
            700.0,
            minimum=1.0,
        ),
        parallel_devices_per_switch=parallel,
        modulation_factor=_number(
            inverter,
            "modulation_factor",
            0.90,
            minimum=0.01,
        ),

        rds_on_ohm_at_25c=_number(
            mosfet,
            "rds_on_ohm_at_25c",
            0.020,
            minimum=1e-9,
        ),
        rds_on_temperature_exponent=_number(
            mosfet,
            "rds_on_temperature_exponent",
            1.273,
            minimum=0.0,
        ),

        eon_reference_j=_number(
            mosfet,
            "eon_reference_j",
            0.000486,
            minimum=0.0,
        ),
        eoff_reference_j=_number(
            mosfet,
            "eoff_reference_j",
            0.000506,
            minimum=0.0,
        ),
        switching_reference_current_a=_number(
            mosfet,
            "switching_reference_current_a",
            50.0,
            minimum=1e-9,
        ),
        switching_reference_voltage_v=_number(
            mosfet,
            "switching_reference_voltage_v",
            520.0,
            minimum=1e-9,
        ),

        tj_reference_c=_number(
            mosfet,
            "tj_reference_c",
            25.0,
        ),
        tj_max_c=_number(
            mosfet,
            "tj_max_c",
            200.0,
        ),
    )


def rds_on_at_temperature(
    config: InverterConfig,
    junction_temperature_c: float,
) -> float:
    t0_k = 25.0 + 273.15
    tj_k = float(junction_temperature_c) + 273.15

    return (
        config.rds_on_ohm_at_25c
        * (tj_k / t0_k)
        ** config.rds_on_temperature_exponent
    )


def _phase_current_from_dc_power(
    dc_power_w: float,
    config: InverterConfig,
) -> tuple[float, float, float]:
    """
    Return:
        phase_current_rms_a,
        served_dc_power_w,
        unserved_power_w

    Uses an SVPWM-style three-phase power relation:
        P ~= sqrt(3) * V_phase_rms * I_phase_rms
    with effective phase voltage represented by modulation_factor * Vdc / sqrt(2).

    This is an engineering approximation for route comparison, not a detailed
    motor dq-axis model.
    """

    requested = float(dc_power_w)
    magnitude = abs(requested)

    effective_voltage = (
        config.modulation_factor
        * config.dc_bus_voltage_v
        / SQRT_2
    )

    denominator = max(
        SQRT_3 * effective_voltage,
        1e-9,
    )

    requested_phase_rms = (
        magnitude / denominator
    )

    max_phase_rms = (
        config.max_phase_current_a_peak
        / SQRT_2
    )

    phase_rms = min(
        requested_phase_rms,
        max_phase_rms,
    )

    served_magnitude = (
        phase_rms
        * denominator
    )

    served = math.copysign(
        served_magnitude,
        requested,
    )

    unserved = requested - served

    return phase_rms, served, unserved


def analyze_inverter_profile(
    longitudinal_result,
    inverter_config: InverterConfig,
) -> InverterResult:
    rows: list[InverterSample] = []

    peak_phase_current = 0.0
    peak_device_current = 0.0
    peak_total_loss = 0.0
    peak_unserved = 0.0
    loss_energy_j = 0.0

    previous_time = None

    total_devices = (
        SWITCH_POSITIONS
        * inverter_config.parallel_devices_per_switch
    )

    for sample in longitudinal_result.samples:
        requested_dc = sample.dc_power_w

        (
            phase_rms,
            served_dc,
            unserved_dc,
        ) = _phase_current_from_dc_power(
            requested_dc,
            inverter_config,
        )

        phase_peak = (
            phase_rms
            * SQRT_2
        )

        device_rms = (
            phase_rms
            / inverter_config.parallel_devices_per_switch
        )

        device_peak = (
            phase_peak
            / inverter_config.parallel_devices_per_switch
        )

        tj_c = inverter_config.tj_reference_c

        rds = rds_on_at_temperature(
            inverter_config,
            tj_c,
        )

        # Aggregate conduction loss over all six switching positions.
        # 0.5 approximates average conduction duty for a two-level inverter.
        conduction_loss = (
            device_rms
            * device_rms
            * rds
            * inverter_config.parallel_devices_per_switch
            * SWITCH_POSITIONS
            * 0.5
        )

        current_scale = (
            device_peak
            / inverter_config.switching_reference_current_a
        )

        voltage_scale = (
            inverter_config.dc_bus_voltage_v
            / inverter_config.switching_reference_voltage_v
        )

        switching_energy_per_event = (
            inverter_config.eon_reference_j
            + inverter_config.eoff_reference_j
        ) * current_scale * voltage_scale

        switching_loss = (
            switching_energy_per_event
            * inverter_config.switching_frequency_hz
            * total_devices
        )

        total_loss = (
            conduction_loss
            + switching_loss
        )

        rows.append(
            InverterSample(
                time_s=sample.time_s,
                dc_power_requested_w=requested_dc,
                dc_power_served_w=served_dc,
                unserved_power_w=unserved_dc,
                phase_current_rms_a=phase_rms,
                phase_current_peak_a=phase_peak,
                device_current_rms_a=device_rms,
                device_current_peak_a=device_peak,
                junction_temperature_c=tj_c,
                rds_on_device_ohm=rds,
                conduction_loss_w=conduction_loss,
                switching_loss_w=switching_loss,
                total_loss_w=total_loss,
            )
        )

        peak_phase_current = max(
            peak_phase_current,
            phase_peak,
        )

        peak_device_current = max(
            peak_device_current,
            device_peak,
        )

        peak_total_loss = max(
            peak_total_loss,
            total_loss,
        )

        peak_unserved = max(
            peak_unserved,
            abs(unserved_dc),
        )

        if previous_time is not None:
            dt = sample.time_s - previous_time
            loss_energy_j += total_loss * dt

        previous_time = sample.time_s

    return InverterResult(
        samples=tuple(rows),
        source_cycle=longitudinal_result.source_cycle,
        vehicle_name=longitudinal_result.vehicle_name,
        peak_phase_current_a=peak_phase_current,
        peak_device_current_a=peak_device_current,
        peak_total_loss_w=peak_total_loss,
        peak_unserved_power_w=peak_unserved,
        loss_energy_wh=loss_energy_j / 3600.0,
    )


def write_inverter_csv(
    result: InverterResult,
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
            f"# peak_phase_current_a={result.peak_phase_current_a:.9f}\n"
        )
        handle.write(
            f"# peak_total_loss_w={result.peak_total_loss_w:.9f}\n"
        )
        handle.write(
            f"# peak_unserved_power_w={result.peak_unserved_power_w:.9f}\n"
        )
        handle.write(
            f"# loss_energy_wh={result.loss_energy_wh:.9f}\n"
        )

        writer = csv.writer(handle)

        writer.writerow(
            [
                "time_s",
                "dc_power_requested_w",
                "dc_power_served_w",
                "unserved_power_w",
                "phase_current_rms_a",
                "phase_current_peak_a",
                "device_current_rms_a",
                "device_current_peak_a",
                "junction_temperature_c",
                "rds_on_device_ohm",
                "conduction_loss_w",
                "switching_loss_w",
                "total_loss_w",
            ]
        )

        for row in result.samples:
            writer.writerow(
                [
                    f"{row.time_s:.6f}",
                    f"{row.dc_power_requested_w:.6f}",
                    f"{row.dc_power_served_w:.6f}",
                    f"{row.unserved_power_w:.6f}",
                    f"{row.phase_current_rms_a:.6f}",
                    f"{row.phase_current_peak_a:.6f}",
                    f"{row.device_current_rms_a:.6f}",
                    f"{row.device_current_peak_a:.6f}",
                    f"{row.junction_temperature_c:.6f}",
                    f"{row.rds_on_device_ohm:.9f}",
                    f"{row.conduction_loss_w:.6f}",
                    f"{row.switching_loss_w:.6f}",
                    f"{row.total_loss_w:.6f}",
                ]
            )

    return path


def run_inverter_analysis(
    drive_cycle_path: str | Path,
    vehicle_config_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> tuple[InverterResult, Path]:
    cycle = load_drive_cycle(
        drive_cycle_path
    )

    profile = build_mission_profile(
        cycle
    )

    vehicle_config = load_vehicle_config(
        vehicle_config_path
    )

    longitudinal_result = analyze_longitudinal_profile(
        profile,
        vehicle_config,
    )

    inverter_config = load_inverter_config(
        vehicle_config_path
    )

    result = analyze_inverter_profile(
        longitudinal_result,
        inverter_config,
    )

    source = Path(drive_cycle_path)

    if output_path is None:
        output_path = source.with_name(
            source.stem + "_inverter_losses.csv"
        )

    written = write_inverter_csv(
        result,
        output_path,
    )

    return result, written
