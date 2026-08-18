from __future__ import annotations

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
from drive_cycles.inverter_electrical import (
    SWITCH_POSITIONS,
    SQRT_2,
    _phase_current_from_dc_power,
    load_inverter_config,
    rds_on_at_temperature,
    switching_energy_scaled,
)


@dataclass(frozen=True)
class FosterPair:
    rth_c_per_w: float
    tau_s: float


@dataclass(frozen=True)
class ThermalConfig:
    fluid_temperature_c: float
    rth_junction_to_fluid_c_per_w: float
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
    fluid_temperature_c: float
    junction_temperature_c: float
    thermal_iterations: int
    converged: bool

    @property
    def case_temperature_c(self) -> float:
        """
        Backward-compatible alias.

        Older route-summary / mission-profile code used the name
        ``case_temperature_c``.  The CAB525F12XM3 model now represents the
        complete junction-to-fluid path, so the old boundary-temperature
        field maps to the fluid temperature.
        """
        return self.fluid_temperature_c


@dataclass(frozen=True)
class ThermalResult:
    samples: tuple[ThermalSample, ...]
    source_cycle: Path
    vehicle_name: str
    peak_phase_current_a: float
    peak_aggregate_loss_w: float
    peak_device_loss_w: float
    peak_fluid_temperature_c: float
    peak_junction_temperature_c: float
    aggregate_loss_energy_j: float
    nonconverged_samples: int
    overtemperature_samples: int

    @property
    def peak_case_temperature_c(self) -> float:
        """
        Backward-compatible alias for F6 / F7 / route_summary.

        The legacy model exposed a case-temperature boundary.  The updated
        CAB525F12XM3 model uses the datasheet junction-to-fluid thermal path,
        therefore this old API name now returns the fluid boundary
        temperature.  Keeping the alias prevents existing analysis layers
        from breaking while preserving the new thermal physics.
        """
        return self.peak_fluid_temperature_c


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(
            f"YAML section {name!r} must be a mapping."
        )
    return value


def load_thermal_config(
    path: str | Path,
) -> ThermalConfig:
    p = Path(path).expanduser().resolve()

    if not p.is_file():
        raise FileNotFoundError(
            f"Vehicle config not found: {p}"
        )

    with p.open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = yaml.safe_load(handle) or {}

    cooling = _section(
        data,
        "cooling",
    )
    mosfet = _section(
        data,
        "mosfet",
    )
    thermal = _section(
        mosfet,
        "thermal",
    )

    raw_pairs = thermal.get(
        "foster_rc_pairs",
        [
            {
                "rth_c_per_w": 0.0013993582,
                "tau_s": 2.6802790e-6,
            },
            {
                "rth_c_per_w": 0.0183206305,
                "tau_s": 3.1548840e-4,
            },
            {
                "rth_c_per_w": 0.0887845942,
                "tau_s": 1.4347792e-2,
            },
            {
                "rth_c_per_w": 0.0364954171,
                "tau_s": 5.0308168e-1,
            },
        ],
    )

    pairs = tuple(
        FosterPair(
            float(x["rth_c_per_w"]),
            float(x["tau_s"]),
        )
        for x in raw_pairs
    )

    if not pairs:
        raise ValueError(
            "At least one Foster RC pair is required."
        )

    for pair in pairs:
        if (
            pair.rth_c_per_w <= 0.0
            or pair.tau_s <= 0.0
        ):
            raise ValueError(
                "All Foster Rth and tau values must be positive."
            )

    rth_total = float(
        thermal.get(
            "rth_junction_to_fluid_c_per_w",
            sum(
                pair.rth_c_per_w
                for pair in pairs
            ),
        )
    )

    pair_sum = sum(
        pair.rth_c_per_w
        for pair in pairs
    )

    if abs(
        pair_sum - rth_total
    ) > 0.002:
        raise ValueError(
            "Foster Rth sum does not match the configured "
            f"junction-to-fluid Rth: {pair_sum:.6f} vs {rth_total:.6f} C/W."
        )

    return ThermalConfig(
        fluid_temperature_c=float(
            cooling.get(
                "coolant_inlet_temp_c",
                60.0,
            )
        ),
        rth_junction_to_fluid_c_per_w=rth_total,
        foster_pairs=pairs,
        max_iterations=int(
            thermal.get(
                "max_iterations",
                30,
            )
        ),
        convergence_tolerance_c=float(
            thermal.get(
                "convergence_tolerance_c",
                0.01,
            )
        ),
        relaxation=min(
            1.0,
            max(
                0.01,
                float(
                    thermal.get(
                        "relaxation",
                        0.5,
                    )
                ),
            ),
        ),
    )


class FosterState:
    """
    Foster representation of the complete CAB525F12XM3 junction-to-fluid
    thermal path.  No extra case-to-coolant resistance is added because the
    datasheet quantity being fitted is already Zth,J-F.
    """

    def __init__(
        self,
        config: ThermalConfig,
    ) -> None:
        self.config = config
        self.branch_rise_c = [
            0.0
            for _ in config.foster_pairs
        ]

    def preview(
        self,
        power_w: float,
        dt_s: float,
    ) -> tuple[list[float], float]:
        updated = []

        for old, pair in zip(
            self.branch_rise_c,
            self.config.foster_pairs,
        ):
            decay = math.exp(
                -dt_s / pair.tau_s
            )

            new = (
                power_w
                * pair.rth_c_per_w
                * (1.0 - decay)
                + old
                * decay
            )

            updated.append(
                new
            )

        return updated, sum(
            updated
        )

    def commit(
        self,
        values: list[float],
    ) -> None:
        self.branch_rise_c = list(
            values
        )


def transient_zth_c_per_w(
    config: ThermalConfig,
    time_s: float,
) -> float:
    t = max(
        0.0,
        float(time_s),
    )

    return sum(
        pair.rth_c_per_w
        * (
            1.0
            - math.exp(
                -t / pair.tau_s
            )
        )
        for pair in config.foster_pairs
    )


def _losses(
    phase_rms: float,
    phase_peak: float,
    tj_c: float,
    inv,
):
    parallel = (
        inv.parallel_devices_per_switch
    )

    device_rms = (
        phase_rms
        / parallel
    )

    rds = rds_on_at_temperature(
        inv,
        tj_c,
    )

    p_cond = (
        device_rms**2
        * rds
        * parallel
        * SWITCH_POSITIONS
        * 0.5
    )

    mean_abs_phase_current = (
        2.0
        * SQRT_2
        / math.pi
        * phase_rms
    )

    switching_current_per_position = (
        mean_abs_phase_current
        / parallel
    )

    eon_j, eoff_j = (
        switching_energy_scaled(
            inv,
            switching_current_per_position,
            inv.dc_bus_voltage_v,
            tj_c,
        )
    )

    p_sw = (
        (eon_j + eoff_j)
        * inv.switching_frequency_hz
        * SWITCH_POSITIONS
        * parallel
    )

    return (
        rds,
        p_cond,
        p_sw,
        p_cond + p_sw,
    )


def analyze_thermal_profile(
    longitudinal_result,
    inv,
    thermal,
):
    state = FosterState(
        thermal
    )

    rows = []

    device_count = (
        SWITCH_POSITIONS
        * inv.parallel_devices_per_switch
    )

    previous_tj = (
        thermal.fluid_temperature_c
    )
    previous_time = None

    peak_phase = 0.0
    peak_agg = 0.0
    peak_device = 0.0
    peak_tj = (
        thermal.fluid_temperature_c
    )

    loss_energy_j = 0.0
    nonconverged = 0
    overtemp = 0

    samples = (
        longitudinal_result.samples
    )

    if not samples:
        raise ValueError(
            "No longitudinal samples."
        )

    default_dt = (
        samples[1].time_s
        - samples[0].time_s
        if len(samples) > 1
        else 0.05
    )

    for s in samples:
        dt = (
            default_dt
            if previous_time is None
            else s.time_s
            - previous_time
        )

        if dt <= 0.0:
            dt = default_dt

        (
            phase_rms,
            served_dc,
            unserved_dc,
        ) = _phase_current_from_dc_power(
            s.dc_power_w,
            inv,
        )

        phase_peak = (
            phase_rms
            * SQRT_2
        )

        device_rms = (
            phase_rms
            / inv.parallel_devices_per_switch
        )

        device_peak = (
            phase_peak
            / inv.parallel_devices_per_switch
        )

        guess = (
            previous_tj
        )
        converged = False
        accepted = None

        for iteration in range(
            1,
            thermal.max_iterations + 1,
        ):
            (
                rds,
                p_cond,
                p_sw,
                p_total,
            ) = _losses(
                phase_rms,
                phase_peak,
                guess,
                inv,
            )

            device_loss = (
                p_total
                / device_count
            )

            branches, rise = (
                state.preview(
                    device_loss,
                    dt,
                )
            )

            new_tj = (
                thermal.fluid_temperature_c
                + rise
            )

            accepted = (
                rds,
                p_cond,
                p_sw,
                p_total,
                device_loss,
                branches,
                iteration,
            )

            if abs(
                new_tj - guess
            ) <= thermal.convergence_tolerance_c:
                guess = new_tj
                converged = True
                break

            guess = (
                thermal.relaxation
                * new_tj
                + (
                    1.0
                    - thermal.relaxation
                )
                * guess
            )

        if accepted is None:
            raise RuntimeError(
                "Thermal solver failed."
            )

        (
            rds,
            p_cond,
            p_sw,
            p_total,
            device_loss,
            branches,
            iterations,
        ) = accepted

        state.commit(
            branches
        )

        final_tj = (
            thermal.fluid_temperature_c
            + sum(branches)
        )

        previous_tj = (
            final_tj
        )

        if not converged:
            nonconverged += 1

        if final_tj > inv.tj_max_c:
            overtemp += 1

        rows.append(
            ThermalSample(
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
                fluid_temperature_c=thermal.fluid_temperature_c,
                junction_temperature_c=final_tj,
                thermal_iterations=iterations,
                converged=converged,
            )
        )

        peak_phase = max(
            peak_phase,
            phase_peak,
        )
        peak_agg = max(
            peak_agg,
            p_total,
        )
        peak_device = max(
            peak_device,
            device_loss,
        )
        peak_tj = max(
            peak_tj,
            final_tj,
        )

        if previous_time is not None:
            loss_energy_j += (
                p_total
                * dt
            )

        previous_time = (
            s.time_s
        )

    return ThermalResult(
        samples=tuple(rows),
        source_cycle=longitudinal_result.source_cycle,
        vehicle_name=longitudinal_result.vehicle_name,
        peak_phase_current_a=peak_phase,
        peak_aggregate_loss_w=peak_agg,
        peak_device_loss_w=peak_device,
        peak_fluid_temperature_c=thermal.fluid_temperature_c,
        peak_junction_temperature_c=peak_tj,
        aggregate_loss_energy_j=loss_energy_j,
        nonconverged_samples=nonconverged,
        overtemperature_samples=overtemp,
    )


def write_thermal_csv(
    result: ThermalResult,
    output_path: str | Path,
) -> Path:
    p = Path(
        output_path
    )

    p.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with p.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.writer(
            handle
        )

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
                "rds_on_device_ohm",
                "aggregate_conduction_loss_w",
                "aggregate_switching_loss_w",
                "aggregate_total_loss_w",
                "device_loss_w",
                "case_temperature_c",
                "fluid_temperature_c",
                "junction_temperature_c",
                "thermal_iterations",
                "converged",
            ]
        )

        for x in result.samples:
            writer.writerow(
                [
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
                    f"{x.fluid_temperature_c:.6f}",
                    f"{x.junction_temperature_c:.6f}",
                    str(
                        x.thermal_iterations
                    ),
                    str(
                        x.converged
                    ),
                ]
            )

    return p


def run_thermal_analysis(
    drive_cycle_path: str | Path,
    vehicle_config_path: str | Path,
    *,
    output_path: str | Path | None = None,
):
    cycle = load_drive_cycle(
        drive_cycle_path
    )

    profile = build_mission_profile(
        cycle
    )

    vehicle_config = load_vehicle_config(
        vehicle_config_path
    )

    longitudinal = (
        analyze_longitudinal_profile(
            profile,
            vehicle_config,
        )
    )

    inverter_config = (
        load_inverter_config(
            vehicle_config_path
        )
    )

    thermal_config = (
        load_thermal_config(
            vehicle_config_path
        )
    )

    result = analyze_thermal_profile(
        longitudinal,
        inverter_config,
        thermal_config,
    )

    source = Path(
        drive_cycle_path
    )

    if output_path is None:
        output_path = source.with_name(
            source.stem
            + "_thermal_trace.csv"
        )

    return (
        result,
        write_thermal_csv(
            result,
            output_path,
        ),
    )
