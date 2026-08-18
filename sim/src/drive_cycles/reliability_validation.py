from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from drive_cycles.rainflow_analysis import RainflowCycle
from drive_cycles.reliability_damage import (
    analyze_reliability,
    load_reliability_config,
    run_reliability_analysis,
)


@dataclass(frozen=True)
class ReliabilityCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ReliabilityModelValidation:
    model: str
    calibrated: bool
    calibration_source: str | None
    checks: tuple[ReliabilityCheck, ...]
    overall_pass: bool


@dataclass(frozen=True)
class ReliabilityMissionValidation:
    source_cycle: str
    total_relative_damage: float
    equivalent_full_cycles: float
    maximum_damage_contribution: float
    most_damaging_cycle_index: int | None
    damage_weighted_delta_tj_c: float
    damage_weighted_tjmax_c: float
    damage_weighted_duration_s: float
    cycles_inside_manufacturer_pc_temperature_envelope: float
    cycles_outside_manufacturer_pc_temperature_envelope: float
    pcsec_equivalent_cycles: float
    pcmin_equivalent_cycles: float
    transition_duration_equivalent_cycles: float
    calibrated: bool


def _single_cycle_result(
    cycle: RainflowCycle,
):
    return SimpleNamespace(
        cycles=(cycle,),
        source_cycle=Path(
            "synthetic_validation"
        ),
    )


def _damage_for(
    config,
    *,
    delta_tj_c,
    maximum_tj_c,
    duration_s,
    count=1.0,
):
    minimum = (
        maximum_tj_c
        - delta_tj_c
    )

    cycle = RainflowCycle(
        delta_tj_c=delta_tj_c,
        mean_tj_c=0.5 * (
            minimum
            + maximum_tj_c
        ),
        count=count,
        minimum_tj_c=minimum,
        maximum_tj_c=maximum_tj_c,
        start_time_s=0.0,
        end_time_s=duration_s,
        duration_s=duration_s,
    )

    return analyze_reliability(
        _single_cycle_result(
            cycle
        ),
        config,
    ).total_relative_damage


def validate_reliability_model(
    vehicle_config_path,
) -> ReliabilityModelValidation:
    config = load_reliability_config(
        vehicle_config_path
    )

    baseline = _damage_for(
        config,
        delta_tj_c=75.0,
        maximum_tj_c=125.0,
        duration_s=5.0,
        count=1.0,
    )

    double_count = _damage_for(
        config,
        delta_tj_c=75.0,
        maximum_tj_c=125.0,
        duration_s=5.0,
        count=2.0,
    )

    higher_delta = _damage_for(
        config,
        delta_tj_c=100.0,
        maximum_tj_c=125.0,
        duration_s=5.0,
    )

    higher_tjmax = _damage_for(
        config,
        delta_tj_c=75.0,
        maximum_tj_c=150.0,
        duration_s=5.0,
    )

    longer_duration = _damage_for(
        config,
        delta_tj_c=75.0,
        maximum_tj_c=125.0,
        duration_s=20.0,
    )

    checks = (
        ReliabilityCheck(
            name="Miner linear cycle-count accumulation",
            passed=abs(
                double_count
                - 2.0 * baseline
            ) <= max(
                1e-12,
                abs(
                    baseline
                ) * 1e-9,
            ),
            detail=(
                f"1 cycle={baseline:.6e}, "
                f"2 cycles={double_count:.6e}"
            ),
        ),
        ReliabilityCheck(
            name="Higher Delta Tj increases damage",
            passed=(
                higher_delta
                > baseline
            ),
            detail=(
                f"75 C={baseline:.6e}, "
                f"100 C={higher_delta:.6e}"
            ),
        ),
        ReliabilityCheck(
            name="Higher Tj,max increases damage",
            passed=(
                higher_tjmax
                > baseline
            ),
            detail=(
                f"125 C={baseline:.6e}, "
                f"150 C={higher_tjmax:.6e}"
            ),
        ),
        ReliabilityCheck(
            name="Longer thermal excursion increases damage",
            passed=(
                longer_duration
                > baseline
            ),
            detail=(
                f"5 s={baseline:.6e}, "
                f"20 s={longer_duration:.6e}"
            ),
        ),
        ReliabilityCheck(
            name="CAB525 absolute lifetime remains unclaimed",
            passed=(
                not config.calibrated
            ),
            detail=(
                "PASS means the GUI will not present relative damage "
                "as manufacturer cycles-to-failure."
            ),
        ),
    )

    return ReliabilityModelValidation(
        model=config.model,
        calibrated=config.calibrated,
        calibration_source=(
            config.calibration_source
        ),
        checks=checks,
        overall_pass=all(
            check.passed
            for check in checks
        ),
    )


def run_reliability_mission_validation(
    drive_cycle_path,
    vehicle_config_path,
) -> ReliabilityMissionValidation:
    result, _ = (
        run_reliability_analysis(
            drive_cycle_path,
            vehicle_config_path,
        )
    )

    return ReliabilityMissionValidation(
        source_cycle=str(
            Path(
                drive_cycle_path
            ).resolve()
        ),
        total_relative_damage=(
            result.total_relative_damage
        ),
        equivalent_full_cycles=(
            result.equivalent_full_cycles
        ),
        maximum_damage_contribution=(
            result.maximum_damage_contribution
        ),
        most_damaging_cycle_index=(
            result.most_damaging_cycle_index
        ),
        damage_weighted_delta_tj_c=(
            result.damage_weighted_delta_tj_c
        ),
        damage_weighted_tjmax_c=(
            result.damage_weighted_tjmax_c
        ),
        damage_weighted_duration_s=(
            result.damage_weighted_duration_s
        ),
        cycles_inside_manufacturer_pc_temperature_envelope=(
            result.cycles_inside_manufacturer_pc_temperature_envelope
        ),
        cycles_outside_manufacturer_pc_temperature_envelope=(
            result.cycles_outside_manufacturer_pc_temperature_envelope
        ),
        pcsec_equivalent_cycles=(
            result.pcsec_equivalent_cycles
        ),
        pcmin_equivalent_cycles=(
            result.pcmin_equivalent_cycles
        ),
        transition_duration_equivalent_cycles=(
            result.transition_duration_equivalent_cycles
        ),
        calibrated=result.calibrated,
    )
