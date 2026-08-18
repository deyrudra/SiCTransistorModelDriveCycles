from __future__ import annotations

"""
Mission-profile SiC power-module relative durability model.

The structure follows the public Wolfspeed power-cycling methodology:
  electro-thermal Tj(t)
      -> rainflow cycles
      -> Delta Tj, Tj,max, cycle-duration stress factors
      -> Palmgren-Miner cumulative damage.

IMPORTANT:
Wolfspeed does not publish CAB525F12XM3-specific cycles-to-failure
coefficients.  Therefore this implementation intentionally reports a
RELATIVE durability index, not absolute years or cycles-to-failure.

The exponents below remain explicit engineering-model parameters.  They are
used consistently across routes so route-to-route ranking is meaningful,
but they must not be presented as manufacturer lifetime coefficients.
"""

from dataclasses import dataclass
import csv
import math
from pathlib import Path
from typing import Any

import yaml

from drive_cycles.rainflow_analysis import (
    RainflowCycle,
    run_rainflow_analysis,
)


BOLTZMANN_EV_PER_K = 8.617333262145e-5


@dataclass(frozen=True)
class ReliabilityConfig:
    model: str
    calibrated: bool
    calibration_source: str | None

    delta_t_reference_c: float
    tj_max_reference_c: float
    duration_reference_s: float

    delta_t_exponent: float
    activation_energy_ev: float
    duration_exponent: float

    minimum_delta_t_c: float

    manufacturer_pc_delta_t_min_c: float
    manufacturer_pc_delta_t_max_c: float
    manufacturer_pc_tjmax_min_c: float
    manufacturer_pc_tjmax_max_c: float
    pcsec_duration_max_s: float
    pcmin_duration_min_s: float


@dataclass(frozen=True)
class DamageCycle:
    cycle_index: int
    delta_tj_c: float
    mean_tj_c: float
    maximum_tj_c: float
    duration_s: float
    count: float

    temperature_swing_factor: float
    maximum_temperature_factor: float
    duration_factor: float
    relative_severity: float

    damage_contribution: float
    within_manufacturer_pc_temperature_envelope: bool
    duration_regime: str


@dataclass(frozen=True)
class ReliabilityResult:
    cycles: tuple[DamageCycle, ...]
    source_cycle: Path

    model: str
    calibrated: bool
    calibration_source: str | None

    total_relative_damage: float
    equivalent_full_cycles: float

    maximum_damage_contribution: float
    most_damaging_cycle_index: int | None

    damage_weighted_delta_tj_c: float
    damage_weighted_mean_tj_c: float
    damage_weighted_tjmax_c: float
    damage_weighted_duration_s: float

    cycles_inside_manufacturer_pc_temperature_envelope: float
    cycles_outside_manufacturer_pc_temperature_envelope: float
    pcsec_equivalent_cycles: float
    pcmin_equivalent_cycles: float
    transition_duration_equivalent_cycles: float


def _section(
    data: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    value = data.get(
        name,
        {},
    )

    if value is None:
        return {}

    if not isinstance(
        value,
        dict,
    ):
        raise ValueError(
            f"YAML section {name!r} must be a mapping."
        )

    return value


def _float_value(
    section,
    key,
    default,
    *,
    minimum=None,
):
    raw = section.get(
        key,
        default,
    )

    try:
        value = float(
            raw
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            f"Reliability value {key!r} must be numeric."
        ) from exc

    if (
        minimum is not None
        and value < minimum
    ):
        raise ValueError(
            f"{key!r} must be >= {minimum}."
        )

    return value


def load_reliability_config(
    path: str | Path,
) -> ReliabilityConfig:
    config_path = (
        Path(path)
        .expanduser()
        .resolve()
    )

    if not config_path.is_file():
        raise FileNotFoundError(
            f"Vehicle config not found: {config_path}"
        )

    with config_path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = yaml.safe_load(
            handle
        ) or {}

    reliability = _section(
        data,
        "reliability",
    )

    model = str(
        reliability.get(
            "model",
            "wolfspeed_structured_relative_damage",
        )
    ).strip()

    source_raw = reliability.get(
        "calibration_source",
        None,
    )

    source = (
        None
        if source_raw in (
            None,
            "",
            "null",
        )
        else str(
            source_raw
        )
    )

    return ReliabilityConfig(
        model=model,
        calibrated=bool(
            reliability.get(
                "calibrated",
                False,
            )
        ),
        calibration_source=source,

        delta_t_reference_c=_float_value(
            reliability,
            "delta_t_reference_c",
            75.0,
            minimum=1e-9,
        ),
        tj_max_reference_c=_float_value(
            reliability,
            "tj_max_reference_c",
            125.0,
        ),
        duration_reference_s=_float_value(
            reliability,
            "duration_reference_s",
            5.0,
            minimum=1e-9,
        ),

        delta_t_exponent=_float_value(
            reliability,
            "delta_t_exponent",
            5.0,
            minimum=0.0,
        ),
        activation_energy_ev=_float_value(
            reliability,
            "activation_energy_ev",
            0.7,
            minimum=0.0,
        ),
        duration_exponent=_float_value(
            reliability,
            "duration_exponent",
            0.3,
            minimum=0.0,
        ),

        minimum_delta_t_c=_float_value(
            reliability,
            "minimum_delta_t_c",
            0.01,
            minimum=0.0,
        ),

        manufacturer_pc_delta_t_min_c=_float_value(
            reliability,
            "manufacturer_pc_delta_t_min_c",
            75.0,
        ),
        manufacturer_pc_delta_t_max_c=_float_value(
            reliability,
            "manufacturer_pc_delta_t_max_c",
            125.0,
        ),
        manufacturer_pc_tjmax_min_c=_float_value(
            reliability,
            "manufacturer_pc_tjmax_min_c",
            125.0,
        ),
        manufacturer_pc_tjmax_max_c=_float_value(
            reliability,
            "manufacturer_pc_tjmax_max_c",
            175.0,
        ),
        pcsec_duration_max_s=_float_value(
            reliability,
            "pcsec_duration_max_s",
            5.0,
            minimum=0.0,
        ),
        pcmin_duration_min_s=_float_value(
            reliability,
            "pcmin_duration_min_s",
            15.0,
            minimum=0.0,
        ),
    )


def _duration_regime(
    duration_s: float,
    config: ReliabilityConfig,
) -> str:
    if duration_s < (
        config.pcsec_duration_max_s
    ):
        return "PCsec-like"

    if duration_s > (
        config.pcmin_duration_min_s
    ):
        return "PCmin-like"

    return "transition"


def _cycle_factors(
    cycle: RainflowCycle,
    config: ReliabilityConfig,
):
    delta_t = max(
        0.0,
        float(
            cycle.delta_tj_c
        ),
    )

    if delta_t < (
        config.minimum_delta_t_c
    ):
        return (
            0.0,
            0.0,
            0.0,
            0.0,
        )

    swing_factor = (
        delta_t
        / config.delta_t_reference_c
    ) ** config.delta_t_exponent

    tjmax_k = (
        float(
            cycle.maximum_tj_c
        )
        + 273.15
    )

    tref_k = (
        config.tj_max_reference_c
        + 273.15
    )

    temperature_factor = math.exp(
        (
            config.activation_energy_ev
            / BOLTZMANN_EV_PER_K
        )
        * (
            1.0 / tref_k
            - 1.0 / tjmax_k
        )
    )

    duration = max(
        float(
            cycle.duration_s
        ),
        1e-6,
    )

    duration_factor = (
        duration
        / config.duration_reference_s
    ) ** config.duration_exponent

    severity = (
        swing_factor
        * temperature_factor
        * duration_factor
    )

    return (
        swing_factor,
        temperature_factor,
        duration_factor,
        severity,
    )


def analyze_reliability(
    rainflow_result,
    config: ReliabilityConfig,
) -> ReliabilityResult:
    rows = []

    total_damage = 0.0
    equivalent = 0.0

    maximum_damage = 0.0
    most_damaging = None

    weighted_delta = 0.0
    weighted_mean = 0.0
    weighted_max = 0.0
    weighted_duration = 0.0

    inside = 0.0
    outside = 0.0
    pcsec = 0.0
    pcmin = 0.0
    transition = 0.0

    for index, cycle in enumerate(
        rainflow_result.cycles,
        start=1,
    ):
        (
            swing_factor,
            temp_factor,
            duration_factor,
            severity,
        ) = _cycle_factors(
            cycle,
            config,
        )

        damage = (
            float(
                cycle.count
            )
            * severity
        )

        within_envelope = (
            config.manufacturer_pc_delta_t_min_c
            <= cycle.delta_tj_c
            <= config.manufacturer_pc_delta_t_max_c
            and
            config.manufacturer_pc_tjmax_min_c
            <= cycle.maximum_tj_c
            <= config.manufacturer_pc_tjmax_max_c
        )

        regime = _duration_regime(
            cycle.duration_s,
            config,
        )

        if within_envelope:
            inside += cycle.count
        else:
            outside += cycle.count

        if regime == "PCsec-like":
            pcsec += cycle.count
        elif regime == "PCmin-like":
            pcmin += cycle.count
        else:
            transition += cycle.count

        rows.append(
            DamageCycle(
                cycle_index=index,
                delta_tj_c=cycle.delta_tj_c,
                mean_tj_c=cycle.mean_tj_c,
                maximum_tj_c=cycle.maximum_tj_c,
                duration_s=cycle.duration_s,
                count=cycle.count,
                temperature_swing_factor=swing_factor,
                maximum_temperature_factor=temp_factor,
                duration_factor=duration_factor,
                relative_severity=severity,
                damage_contribution=damage,
                within_manufacturer_pc_temperature_envelope=within_envelope,
                duration_regime=regime,
            )
        )

        equivalent += (
            cycle.count
        )
        total_damage += damage

        weighted_delta += (
            cycle.delta_tj_c
            * damage
        )
        weighted_mean += (
            cycle.mean_tj_c
            * damage
        )
        weighted_max += (
            cycle.maximum_tj_c
            * damage
        )
        weighted_duration += (
            cycle.duration_s
            * damage
        )

        if (
            most_damaging is None
            or damage > maximum_damage
        ):
            maximum_damage = (
                damage
            )
            most_damaging = (
                index
            )

    denominator = max(
        total_damage,
        1e-300,
    )

    return ReliabilityResult(
        cycles=tuple(
            rows
        ),
        source_cycle=(
            rainflow_result.source_cycle
        ),
        model=config.model,
        calibrated=config.calibrated,
        calibration_source=(
            config.calibration_source
        ),
        total_relative_damage=(
            total_damage
        ),
        equivalent_full_cycles=(
            equivalent
        ),
        maximum_damage_contribution=(
            maximum_damage
        ),
        most_damaging_cycle_index=(
            most_damaging
        ),
        damage_weighted_delta_tj_c=(
            weighted_delta
            / denominator
            if total_damage > 0.0
            else 0.0
        ),
        damage_weighted_mean_tj_c=(
            weighted_mean
            / denominator
            if total_damage > 0.0
            else 0.0
        ),
        damage_weighted_tjmax_c=(
            weighted_max
            / denominator
            if total_damage > 0.0
            else 0.0
        ),
        damage_weighted_duration_s=(
            weighted_duration
            / denominator
            if total_damage > 0.0
            else 0.0
        ),
        cycles_inside_manufacturer_pc_temperature_envelope=inside,
        cycles_outside_manufacturer_pc_temperature_envelope=outside,
        pcsec_equivalent_cycles=pcsec,
        pcmin_equivalent_cycles=pcmin,
        transition_duration_equivalent_cycles=transition,
    )


def write_reliability_csv(
    result,
    output_path,
):
    path = Path(
        output_path
    )

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
            f"# model={result.model}\n"
        )
        handle.write(
            f"# calibrated={result.calibrated}\n"
        )
        handle.write(
            f"# calibration_source={result.calibration_source}\n"
        )
        handle.write(
            f"# total_relative_damage={result.total_relative_damage:.12e}\n"
        )
        handle.write(
            f"# equivalent_full_cycles={result.equivalent_full_cycles:.9f}\n"
        )
        handle.write(
            f"# cycles_inside_manufacturer_pc_temperature_envelope="
            f"{result.cycles_inside_manufacturer_pc_temperature_envelope:.9f}\n"
        )
        handle.write(
            f"# cycles_outside_manufacturer_pc_temperature_envelope="
            f"{result.cycles_outside_manufacturer_pc_temperature_envelope:.9f}\n"
        )

        writer = csv.writer(
            handle
        )

        writer.writerow(
            [
                "cycle_index",
                "delta_tj_c",
                "mean_tj_c",
                "maximum_tj_c",
                "duration_s",
                "count",
                "temperature_swing_factor",
                "maximum_temperature_factor",
                "duration_factor",
                "relative_severity",
                "damage_contribution",
                "within_manufacturer_pc_temperature_envelope",
                "duration_regime",
            ]
        )

        for row in result.cycles:
            writer.writerow(
                [
                    row.cycle_index,
                    f"{row.delta_tj_c:.9f}",
                    f"{row.mean_tj_c:.9f}",
                    f"{row.maximum_tj_c:.9f}",
                    f"{row.duration_s:.9f}",
                    f"{row.count:.1f}",
                    f"{row.temperature_swing_factor:.12e}",
                    f"{row.maximum_temperature_factor:.12e}",
                    f"{row.duration_factor:.12e}",
                    f"{row.relative_severity:.12e}",
                    f"{row.damage_contribution:.12e}",
                    str(
                        row.within_manufacturer_pc_temperature_envelope
                    ),
                    row.duration_regime,
                ]
            )

    return path


def run_reliability_analysis(
    drive_cycle_path,
    vehicle_config_path,
    *,
    output_path=None,
):
    rainflow_result, _ = (
        run_rainflow_analysis(
            drive_cycle_path,
            vehicle_config_path,
        )
    )

    config = load_reliability_config(
        vehicle_config_path
    )

    result = analyze_reliability(
        rainflow_result,
        config,
    )

    source = Path(
        drive_cycle_path
    )

    if output_path is None:
        output_path = (
            source.with_name(
                source.stem
                + "_relative_damage.csv"
            )
        )

    return (
        result,
        write_reliability_csv(
            result,
            output_path,
        ),
    )
