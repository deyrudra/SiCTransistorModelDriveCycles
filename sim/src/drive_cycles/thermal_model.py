from __future__ import annotations

from dataclasses import dataclass
import csv
import math
from pathlib import Path
from typing import Any

import yaml

from drive_cycles.drive_cycle_loader import build_mission_profile, load_drive_cycle
from drive_cycles.longitudinal_profile import analyze_longitudinal_profile
from drive_cycles.vehicle_config import load_vehicle_config
from drive_cycles.inverter_electrical import (
    SWITCH_POSITIONS,
    _phase_current_from_dc_power,
    load_inverter_config,
    rds_on_at_temperature,
)


@dataclass(frozen=True)
class FosterPair:
    rth_c_per_w: float
    tau_s: float


@dataclass(frozen=True)
class ThermalConfig:
    coolant_temperature_c: float
    rth_case_to_coolant_c_per_w: float
    foster_pairs: tuple[FosterPair, ...]
    max_iterations: int
    convergence_tolerance_c: float
    relaxation: float


@dataclass(frozen=True)
class ThermalSample:
    time_s: float
    dc_power_requested_w: float
    dc_power_served_w: float
    unserved_power_w: float
    phase_current_rms_a: float
    phase_current_peak_a: float
    device_current_rms_a: float
    device_current_peak_a: float
    rds_on_device_ohm: float
    aggregate_conduction_loss_w: float
    aggregate_switching_loss_w: float
    aggregate_total_loss_w: float
    device_loss_w: float
    case_temperature_c: float
    junction_temperature_c: float
    thermal_iterations: int
    converged: bool


@dataclass(frozen=True)
class ThermalResult:
    samples: tuple[ThermalSample, ...]
    source_cycle: Path
    vehicle_name: str
    peak_phase_current_a: float
    peak_aggregate_loss_w: float
    peak_device_loss_w: float
    peak_case_temperature_c: float
    peak_junction_temperature_c: float
    aggregate_loss_energy_j: float
    nonconverged_samples: int
    overtemperature_samples: int


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML section {name!r} must be a mapping.")
    return value


def load_thermal_config(path: str | Path) -> ThermalConfig:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Vehicle config not found: {p}")

    with p.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    cooling = _section(data, "cooling")
    mosfet = _section(data, "mosfet")
    thermal = _section(mosfet, "thermal")

    raw_pairs = thermal.get("foster_rc_pairs", [
        {"rth_c_per_w": 0.0216, "tau_s": 0.000077},
        {"rth_c_per_w": 0.0703, "tau_s": 0.001554},
        {"rth_c_per_w": 0.1668, "tau_s": 0.016634},
        {"rth_c_per_w": 0.1613, "tau_s": 0.142761},
    ])

    pairs = tuple(
        FosterPair(float(x["rth_c_per_w"]), float(x["tau_s"]))
        for x in raw_pairs
    )

    return ThermalConfig(
        coolant_temperature_c=float(cooling.get("coolant_inlet_temp_c", 65.0)),
        rth_case_to_coolant_c_per_w=float(
            thermal.get("rth_case_to_coolant_c_per_w", 0.05)
        ),
        foster_pairs=pairs,
        max_iterations=int(thermal.get("max_iterations", 30)),
        convergence_tolerance_c=float(
            thermal.get("convergence_tolerance_c", 0.01)
        ),
        relaxation=min(1.0, max(0.01, float(thermal.get("relaxation", 0.5)))),
    )


class FosterState:
    def __init__(self, config: ThermalConfig) -> None:
        self.config = config
        self.branch_rise_c = [0.0 for _ in config.foster_pairs]

    def preview(self, power_w: float, dt_s: float) -> tuple[list[float], float]:
        updated = []
        for old, pair in zip(self.branch_rise_c, self.config.foster_pairs):
            decay = math.exp(-dt_s / pair.tau_s)
            new = power_w * pair.rth_c_per_w * (1.0 - decay) + old * decay
            updated.append(new)
        return updated, sum(updated)

    def commit(self, values: list[float]) -> None:
        self.branch_rise_c = list(values)


def _losses(phase_rms, phase_peak, tj_c, inv):
    parallel = inv.parallel_devices_per_switch
    device_rms = phase_rms / parallel
    device_peak = phase_peak / parallel

    rds = rds_on_at_temperature(inv, tj_c)

    p_cond = (
        device_rms**2
        * rds
        * parallel
        * SWITCH_POSITIONS
        * 0.5
    )

    current_scale = device_peak / inv.switching_reference_current_a
    voltage_scale = inv.dc_bus_voltage_v / inv.switching_reference_voltage_v

    e_sw = (
        inv.eon_reference_j + inv.eoff_reference_j
    ) * current_scale * voltage_scale

    p_sw = (
        e_sw
        * inv.switching_frequency_hz
        * SWITCH_POSITIONS
        * parallel
    )

    return rds, p_cond, p_sw, p_cond + p_sw


def analyze_thermal_profile(longitudinal_result, inv, thermal):
    state = FosterState(thermal)
    rows = []
    device_count = SWITCH_POSITIONS * inv.parallel_devices_per_switch

    previous_tj = thermal.coolant_temperature_c
    previous_time = None

    peak_phase = 0.0
    peak_agg = 0.0
    peak_device = 0.0
    peak_case = thermal.coolant_temperature_c
    peak_tj = thermal.coolant_temperature_c
    loss_energy_j = 0.0
    nonconverged = 0
    overtemp = 0

    samples = longitudinal_result.samples
    if not samples:
        raise ValueError("No longitudinal samples.")

    default_dt = (
        samples[1].time_s - samples[0].time_s
        if len(samples) > 1
        else 0.05
    )

    for s in samples:
        dt = default_dt if previous_time is None else s.time_s - previous_time

        phase_rms, served_dc, unserved_dc = _phase_current_from_dc_power(
            s.dc_power_w, inv
        )
        phase_peak = phase_rms * math.sqrt(2.0)
        device_rms = phase_rms / inv.parallel_devices_per_switch
        device_peak = phase_peak / inv.parallel_devices_per_switch

        guess = previous_tj
        converged = False

        accepted = None

        for iteration in range(1, thermal.max_iterations + 1):
            rds, p_cond, p_sw, p_total = _losses(
                phase_rms, phase_peak, guess, inv
            )

            device_loss = p_total / device_count
            case_temp = (
                thermal.coolant_temperature_c
                + device_loss * thermal.rth_case_to_coolant_c_per_w
            )

            branches, rise = state.preview(device_loss, dt)
            new_tj = case_temp + rise

            accepted = (
                rds, p_cond, p_sw, p_total,
                device_loss, case_temp, branches,
                iteration
            )

            if abs(new_tj - guess) <= thermal.convergence_tolerance_c:
                guess = new_tj
                converged = True
                break

            guess = (
                thermal.relaxation * new_tj
                + (1.0 - thermal.relaxation) * guess
            )

        if accepted is None:
            raise RuntimeError("Thermal solver failed.")

        (
            rds, p_cond, p_sw, p_total,
            device_loss, case_temp, branches,
            iterations
        ) = accepted

        state.commit(branches)
        final_tj = case_temp + sum(branches)
        previous_tj = final_tj

        if not converged:
            nonconverged += 1
        if final_tj > inv.tj_max_c:
            overtemp += 1

        rows.append(ThermalSample(
            time_s=s.time_s,
            dc_power_requested_w=s.dc_power_w,
            dc_power_served_w=served_dc,
            unserved_power_w=unserved_dc,
            phase_current_rms_a=phase_rms,
            phase_current_peak_a=phase_peak,
            device_current_rms_a=device_rms,
            device_current_peak_a=device_peak,
            rds_on_device_ohm=rds,
            aggregate_conduction_loss_w=p_cond,
            aggregate_switching_loss_w=p_sw,
            aggregate_total_loss_w=p_total,
            device_loss_w=device_loss,
            case_temperature_c=case_temp,
            junction_temperature_c=final_tj,
            thermal_iterations=iterations,
            converged=converged,
        ))

        peak_phase = max(peak_phase, phase_peak)
        peak_agg = max(peak_agg, p_total)
        peak_device = max(peak_device, device_loss)
        peak_case = max(peak_case, case_temp)
        peak_tj = max(peak_tj, final_tj)

        if previous_time is not None:
            loss_energy_j += p_total * dt
        previous_time = s.time_s

    return ThermalResult(
        samples=tuple(rows),
        source_cycle=longitudinal_result.source_cycle,
        vehicle_name=longitudinal_result.vehicle_name,
        peak_phase_current_a=peak_phase,
        peak_aggregate_loss_w=peak_agg,
        peak_device_loss_w=peak_device,
        peak_case_temperature_c=peak_case,
        peak_junction_temperature_c=peak_tj,
        aggregate_loss_energy_j=loss_energy_j,
        nonconverged_samples=nonconverged,
        overtemperature_samples=overtemp,
    )


def write_thermal_csv(result: ThermalResult, output_path: str | Path) -> Path:
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with p.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "time_s",
            "dc_power_requested_w",
            "dc_power_served_w",
            "unserved_power_w",
            "phase_current_rms_a",
            "phase_current_peak_a",
            "device_current_rms_a",
            "device_current_peak_a",
            "rds_on_device_ohm",
            "aggregate_conduction_loss_w",
            "aggregate_switching_loss_w",
            "aggregate_total_loss_w",
            "device_loss_w",
            "case_temperature_c",
            "junction_temperature_c",
            "thermal_iterations",
            "converged",
        ])

        for x in result.samples:
            writer.writerow([
                f"{x.time_s:.6f}",
                f"{x.dc_power_requested_w:.6f}",
                f"{x.dc_power_served_w:.6f}",
                f"{x.unserved_power_w:.6f}",
                f"{x.phase_current_rms_a:.6f}",
                f"{x.phase_current_peak_a:.6f}",
                f"{x.device_current_rms_a:.6f}",
                f"{x.device_current_peak_a:.6f}",
                f"{x.rds_on_device_ohm:.9f}",
                f"{x.aggregate_conduction_loss_w:.6f}",
                f"{x.aggregate_switching_loss_w:.6f}",
                f"{x.aggregate_total_loss_w:.6f}",
                f"{x.device_loss_w:.6f}",
                f"{x.case_temperature_c:.6f}",
                f"{x.junction_temperature_c:.6f}",
                str(x.thermal_iterations),
                str(x.converged),
            ])
    return p


def run_thermal_analysis(
    drive_cycle_path: str | Path,
    vehicle_config_path: str | Path,
    *,
    output_path: str | Path | None = None,
):
    cycle = load_drive_cycle(drive_cycle_path)
    profile = build_mission_profile(cycle)
    vehicle_config = load_vehicle_config(vehicle_config_path)
    longitudinal = analyze_longitudinal_profile(profile, vehicle_config)
    inverter_config = load_inverter_config(vehicle_config_path)
    thermal_config = load_thermal_config(vehicle_config_path)

    result = analyze_thermal_profile(
        longitudinal,
        inverter_config,
        thermal_config,
    )

    source = Path(drive_cycle_path)
    if output_path is None:
        output_path = source.with_name(source.stem + "_thermal_trace.csv")

    return result, write_thermal_csv(result, output_path)
