from __future__ import annotations

"""
Relative SiC thermal-cycling damage model.

This module intentionally reports RELATIVE damage unless the coefficients have
been calibrated against device-specific power-cycling data.

For each rainflow cycle:

    severity =
        (DeltaTj / DeltaT_ref) ** m
        * exp[
            Ea / kB
            * (1 / T_ref - 1 / T_mean)
        ]

    damage_contribution =
        cycle_count * severity

The accumulated route metric is:

    total_relative_damage = sum(damage_contribution)

Interpretation:
- larger DeltaTj -> more damage
- higher mean junction temperature -> more damage
- more counted cycles -> more damage

This is suitable for comparing candidate routes with the SAME device/model
parameters. It is not an absolute cycles-to-failure prediction unless calibrated.
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
    mean_tj_reference_c: float
    coffin_manson_exponent: float
    activation_energy_ev: float

    minimum_delta_t_c: float


@dataclass(frozen=True)
class DamageCycle:
    cycle_index: int

    delta_tj_c: float
    mean_tj_c: float
    count: float

    temperature_swing_factor: float
    arrhenius_factor: float
    relative_severity: float

    relative_cycles_to_failure: float
    damage_contribution: float


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


def _section(
    data: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    value = data.get(name, {})

    if value is None:
        return {}

    if not isinstance(value, dict):
        raise ValueError(
            f"YAML section {name!r} must be a mapping."
        )

    return value


def _float_value(
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
            f"Reliability value {key!r} must be numeric, got {raw!r}."
        ) from exc

    if minimum is not None and value < minimum:
        raise ValueError(
            f"Reliability value {key!r} must be >= {minimum}, got {value}."
        )

    return value


def load_reliability_config(
    path: str | Path,
) -> ReliabilityConfig:
    config_path = Path(path).expanduser().resolve()

    if not config_path.is_file():
        raise FileNotFoundError(
            f"Vehicle config not found: {config_path}"
        )

    with config_path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError(
            "Vehicle YAML root must be a mapping."
        )

    reliability = _section(
        data,
        "reliability",
    )

    model = str(
        reliability.get(
            "model",
            "coffin_manson_arrhenius",
        )
    ).strip()

    if model != "coffin_manson_arrhenius":
        raise ValueError(
            "This implementation currently supports only "
            "'coffin_manson_arrhenius'."
        )

    calibrated = bool(
        reliability.get(
            "calibrated",
            False,
        )
    )

    source_raw = reliability.get(
        "calibration_source",
        None,
    )

    calibration_source = (
        None
        if source_raw in (None, "", "null")
        else str(source_raw)
    )

    return ReliabilityConfig(
        model=model,
        calibrated=calibrated,
        calibration_source=calibration_source,

        delta_t_reference_c=_float_value(
            reliability,
            "delta_t_reference_c",
            40.0,
            minimum=1e-9,
        ),

        mean_tj_reference_c=_float_value(
            reliability,
            "mean_tj_reference_c",
            100.0,
        ),

        coffin_manson_exponent=_float_value(
            reliability,
            "coffin_manson_exponent",
            5.0,
            minimum=0.0,
        ),

        activation_energy_ev=_float_value(
            reliability,
            "activation_energy_ev",
            0.7,
            minimum=0.0,
        ),

        minimum_delta_t_c=_float_value(
            reliability,
            "minimum_delta_t_c",
            0.01,
            minimum=0.0,
        ),
    )


def _cycle_severity(
    cycle: RainflowCycle,
    config: ReliabilityConfig,
) -> tuple[float, float, float]:
    delta_t = max(
        0.0,
        float(cycle.delta_tj_c),
    )

    if delta_t < config.minimum_delta_t_c:
        return 0.0, 0.0, 0.0

    temperature_swing_factor = (
        delta_t
        / config.delta_t_reference_c
    ) ** config.coffin_manson_exponent

    mean_t_k = (
        float(cycle.mean_tj_c)
        + 273.15
    )

    reference_t_k = (
        config.mean_tj_reference_c
        + 273.15
    )

    if mean_t_k <= 0.0:
        raise ValueError(
            "Rainflow mean junction temperature is below absolute zero."
        )

    arrhenius_factor = math.exp(
        (
            config.activation_energy_ev
            / BOLTZMANN_EV_PER_K
        )
        * (
            1.0 / reference_t_k
            - 1.0 / mean_t_k
        )
    )

    severity = (
        temperature_swing_factor
        * arrhenius_factor
    )

    return (
        temperature_swing_factor,
        arrhenius_factor,
        severity,
    )


def analyze_reliability(
    rainflow_result,
    config: ReliabilityConfig,
) -> ReliabilityResult:
    rows: list[DamageCycle] = []

    total_damage = 0.0
    equivalent_full_cycles = 0.0

    maximum_damage = 0.0
    most_damaging_index = None

    weighted_delta_numerator = 0.0
    weighted_mean_t_numerator = 0.0

    for index, cycle in enumerate(
        rainflow_result.cycles,
        start=1,
    ):
        (
            swing_factor,
            arrhenius_factor,
            severity,
        ) = _cycle_severity(
            cycle,
            config,
        )

        if severity > 0.0:
            relative_cycles_to_failure = (
                1.0 / severity
            )
        else:
            relative_cycles_to_failure = math.inf

        damage = (
            float(cycle.count)
            * severity
        )

        rows.append(
            DamageCycle(
                cycle_index=index,
                delta_tj_c=cycle.delta_tj_c,
                mean_tj_c=cycle.mean_tj_c,
                count=cycle.count,
                temperature_swing_factor=swing_factor,
                arrhenius_factor=arrhenius_factor,
                relative_severity=severity,
                relative_cycles_to_failure=relative_cycles_to_failure,
                damage_contribution=damage,
            )
        )

        equivalent_full_cycles += (
            cycle.count
        )

        total_damage += damage

        weighted_delta_numerator += (
            cycle.delta_tj_c
            * damage
        )

        weighted_mean_t_numerator += (
            cycle.mean_tj_c
            * damage
        )

        if (
            most_damaging_index is None
            or damage > maximum_damage
        ):
            maximum_damage = damage
            most_damaging_index = index

    if total_damage > 0.0:
        damage_weighted_delta = (
            weighted_delta_numerator
            / total_damage
        )

        damage_weighted_mean_t = (
            weighted_mean_t_numerator
            / total_damage
        )
    else:
        damage_weighted_delta = 0.0
        damage_weighted_mean_t = 0.0

    return ReliabilityResult(
        cycles=tuple(rows),
        source_cycle=rainflow_result.source_cycle,
        model=config.model,
        calibrated=config.calibrated,
        calibration_source=config.calibration_source,
        total_relative_damage=total_damage,
        equivalent_full_cycles=equivalent_full_cycles,
        maximum_damage_contribution=maximum_damage,
        most_damaging_cycle_index=most_damaging_index,
        damage_weighted_delta_tj_c=damage_weighted_delta,
        damage_weighted_mean_tj_c=damage_weighted_mean_t,
    )


def write_reliability_csv(
    result: ReliabilityResult,
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
            f"# maximum_damage_contribution={result.maximum_damage_contribution:.12e}\n"
        )
        handle.write(
            f"# most_damaging_cycle_index={result.most_damaging_cycle_index}\n"
        )
        handle.write(
            f"# damage_weighted_delta_tj_c={result.damage_weighted_delta_tj_c:.9f}\n"
        )
        handle.write(
            f"# damage_weighted_mean_tj_c={result.damage_weighted_mean_tj_c:.9f}\n"
        )

        writer = csv.writer(
            handle
        )

        writer.writerow(
            [
                "cycle_index",
                "delta_tj_c",
                "mean_tj_c",
                "count",
                "temperature_swing_factor",
                "arrhenius_factor",
                "relative_severity",
                "relative_cycles_to_failure",
                "damage_contribution",
            ]
        )

        for row in result.cycles:
            relative_ctf = (
                "inf"
                if math.isinf(
                    row.relative_cycles_to_failure
                )
                else f"{row.relative_cycles_to_failure:.12e}"
            )

            writer.writerow(
                [
                    row.cycle_index,
                    f"{row.delta_tj_c:.9f}",
                    f"{row.mean_tj_c:.9f}",
                    f"{row.count:.1f}",
                    f"{row.temperature_swing_factor:.12e}",
                    f"{row.arrhenius_factor:.12e}",
                    f"{row.relative_severity:.12e}",
                    relative_ctf,
                    f"{row.damage_contribution:.12e}",
                ]
            )

    return path


def run_reliability_analysis(
    drive_cycle_path: str | Path,
    vehicle_config_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> tuple[ReliabilityResult, Path]:
    rainflow_result, _ = run_rainflow_analysis(
        drive_cycle_path,
        vehicle_config_path,
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
        output_path = source.with_name(
            source.stem
            + "_relative_damage.csv"
        )

    written = write_reliability_csv(
        result,
        output_path,
    )

    return result, written
