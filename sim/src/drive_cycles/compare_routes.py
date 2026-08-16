from __future__ import annotations

"""
Compare multiple analyzed Stuttgart routes.

Inputs:
    one or more *_summary.json files produced by route_summary.py

Default score:
    0.40 * normalized_duration
  + 0.30 * normalized_net_energy
  + 0.30 * normalized_relative_damage

Lower score is better.

Peak junction temperature can be:
- reported only, or
- enforced as a hard feasibility limit.

Normalization is min-max over the candidate set:
    (x - min) / (max - min)

If all candidates have the same value for a metric, that normalized metric is
set to 0 for every candidate so it does not affect ranking.
"""

from dataclasses import dataclass, asdict
import csv
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class RouteCandidate:
    route_name: str
    summary_path: Path
    source_cycle: Path

    duration_s: float
    distance_km: float
    net_dc_energy_kwh: float
    peak_junction_temperature_c: float
    total_relative_damage: float

    reliability_calibrated: bool
    nonconverged_thermal_samples: int
    overtemperature_samples: int


@dataclass(frozen=True)
class RouteScore:
    rank: int | None
    feasible: bool
    rejection_reason: str | None

    route_name: str
    summary_path: Path
    source_cycle: Path

    duration_s: float
    distance_km: float
    net_dc_energy_kwh: float
    peak_junction_temperature_c: float
    total_relative_damage: float

    normalized_time: float
    normalized_energy: float
    normalized_damage: float

    weighted_score: float | None


@dataclass(frozen=True)
class RouteComparisonResult:
    routes: tuple[RouteScore, ...]
    time_weight: float
    energy_weight: float
    damage_weight: float
    peak_tj_limit_c: float | None
    best_route_name: str | None


def _read_summary_json(
    path: str | Path,
) -> RouteCandidate:
    summary_path = Path(path).expanduser().resolve()

    if not summary_path.is_file():
        raise FileNotFoundError(
            f"Route summary JSON not found: {summary_path}"
        )

    with summary_path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = json.load(handle)

    required = [
        "source_cycle",
        "duration_s",
        "distance_km",
        "net_dc_energy_kwh",
        "peak_junction_temperature_c",
        "total_relative_damage",
        "reliability_calibrated",
        "nonconverged_thermal_samples",
        "overtemperature_samples",
    ]

    missing = [
        key
        for key in required
        if key not in data
    ]

    if missing:
        raise ValueError(
            f"{summary_path} is missing required fields: "
            + ", ".join(missing)
        )

    source_cycle = Path(
        str(data["source_cycle"])
    )

    route_name = source_cycle.stem

    if route_name.endswith("_summary"):
        route_name = route_name[:-8]

    return RouteCandidate(
        route_name=route_name,
        summary_path=summary_path,
        source_cycle=source_cycle,
        duration_s=float(data["duration_s"]),
        distance_km=float(data["distance_km"]),
        net_dc_energy_kwh=float(data["net_dc_energy_kwh"]),
        peak_junction_temperature_c=float(
            data["peak_junction_temperature_c"]
        ),
        total_relative_damage=float(
            data["total_relative_damage"]
        ),
        reliability_calibrated=bool(
            data["reliability_calibrated"]
        ),
        nonconverged_thermal_samples=int(
            data["nonconverged_thermal_samples"]
        ),
        overtemperature_samples=int(
            data["overtemperature_samples"]
        ),
    )


def _normalize(
    values: list[float],
) -> list[float]:
    if not values:
        return []

    minimum = min(values)
    maximum = max(values)

    span = maximum - minimum

    if abs(span) <= 1e-15:
        return [
            0.0
            for _ in values
        ]

    return [
        (value - minimum) / span
        for value in values
    ]


def compare_route_summaries(
    summary_paths: Iterable[str | Path],
    *,
    time_weight: float = 0.40,
    energy_weight: float = 0.30,
    damage_weight: float = 0.30,
    peak_tj_limit_c: float | None = None,
) -> RouteComparisonResult:
    candidates = [
        _read_summary_json(path)
        for path in summary_paths
    ]

    if len(candidates) < 2:
        raise ValueError(
            "At least two route summary JSON files are required."
        )

    weights = [
        float(time_weight),
        float(energy_weight),
        float(damage_weight),
    ]

    if any(weight < 0.0 for weight in weights):
        raise ValueError(
            "Route-score weights must be non-negative."
        )

    weight_sum = sum(weights)

    if weight_sum <= 0.0:
        raise ValueError(
            "At least one route-score weight must be greater than zero."
        )

    # Normalize weights so users may provide either fractions or arbitrary
    # positive relative weights.
    time_weight = weights[0] / weight_sum
    energy_weight = weights[1] / weight_sum
    damage_weight = weights[2] / weight_sum

    feasibility: list[tuple[bool, str | None]] = []

    for candidate in candidates:
        reasons: list[str] = []

        if candidate.nonconverged_thermal_samples > 0:
            reasons.append(
                "thermal solver did not converge"
            )

        if candidate.overtemperature_samples > 0:
            reasons.append(
                "configured Tj maximum exceeded"
            )

        if (
            peak_tj_limit_c is not None
            and candidate.peak_junction_temperature_c
            > peak_tj_limit_c
        ):
            reasons.append(
                f"peak Tj exceeds {peak_tj_limit_c:.2f} C limit"
            )

        feasibility.append(
            (
                len(reasons) == 0,
                "; ".join(reasons)
                if reasons
                else None,
            )
        )

    # Normalize over all candidate routes. This keeps the comparison context
    # transparent, while infeasible routes are still excluded from ranking.
    normalized_time = _normalize(
        [
            c.duration_s
            for c in candidates
        ]
    )

    normalized_energy = _normalize(
        [
            c.net_dc_energy_kwh
            for c in candidates
        ]
    )

    normalized_damage = _normalize(
        [
            c.total_relative_damage
            for c in candidates
        ]
    )

    preliminary: list[dict] = []

    for index, candidate in enumerate(candidates):
        feasible, reason = feasibility[index]

        score = None

        if feasible:
            score = (
                time_weight
                * normalized_time[index]
                + energy_weight
                * normalized_energy[index]
                + damage_weight
                * normalized_damage[index]
            )

        preliminary.append(
            {
                "candidate": candidate,
                "feasible": feasible,
                "reason": reason,
                "normalized_time": normalized_time[index],
                "normalized_energy": normalized_energy[index],
                "normalized_damage": normalized_damage[index],
                "score": score,
            }
        )

    feasible_rows = [
        row
        for row in preliminary
        if row["feasible"]
    ]

    feasible_rows.sort(
        key=lambda row: (
            row["score"],
            row["candidate"].duration_s,
        )
    )

    ranks = {
        row["candidate"].summary_path: rank
        for rank, row in enumerate(
            feasible_rows,
            start=1,
        )
    }

    route_scores: list[RouteScore] = []

    for row in preliminary:
        candidate = row["candidate"]

        route_scores.append(
            RouteScore(
                rank=ranks.get(
                    candidate.summary_path
                ),
                feasible=row["feasible"],
                rejection_reason=row["reason"],

                route_name=candidate.route_name,
                summary_path=candidate.summary_path,
                source_cycle=candidate.source_cycle,

                duration_s=candidate.duration_s,
                distance_km=candidate.distance_km,
                net_dc_energy_kwh=candidate.net_dc_energy_kwh,
                peak_junction_temperature_c=(
                    candidate.peak_junction_temperature_c
                ),
                total_relative_damage=(
                    candidate.total_relative_damage
                ),

                normalized_time=row["normalized_time"],
                normalized_energy=row["normalized_energy"],
                normalized_damage=row["normalized_damage"],

                weighted_score=row["score"],
            )
        )

    route_scores.sort(
        key=lambda route: (
            not route.feasible,
            route.rank if route.rank is not None else 10**9,
            route.route_name,
        )
    )

    best_route_name = (
        feasible_rows[0]["candidate"].route_name
        if feasible_rows
        else None
    )

    return RouteComparisonResult(
        routes=tuple(route_scores),
        time_weight=time_weight,
        energy_weight=energy_weight,
        damage_weight=damage_weight,
        peak_tj_limit_c=peak_tj_limit_c,
        best_route_name=best_route_name,
    )


def write_comparison_csv(
    result: RouteComparisonResult,
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
            f"# time_weight={result.time_weight:.9f}\n"
        )
        handle.write(
            f"# energy_weight={result.energy_weight:.9f}\n"
        )
        handle.write(
            f"# damage_weight={result.damage_weight:.9f}\n"
        )
        handle.write(
            f"# peak_tj_limit_c={result.peak_tj_limit_c}\n"
        )
        handle.write(
            f"# best_route_name={result.best_route_name}\n"
        )

        writer = csv.writer(handle)

        writer.writerow(
            [
                "rank",
                "feasible",
                "route_name",
                "duration_s",
                "distance_km",
                "net_dc_energy_kwh",
                "peak_junction_temperature_c",
                "total_relative_damage",
                "normalized_time",
                "normalized_energy",
                "normalized_damage",
                "weighted_score",
                "rejection_reason",
                "summary_path",
                "source_cycle",
            ]
        )

        for route in result.routes:
            writer.writerow(
                [
                    ""
                    if route.rank is None
                    else route.rank,
                    route.feasible,
                    route.route_name,
                    f"{route.duration_s:.9f}",
                    f"{route.distance_km:.9f}",
                    f"{route.net_dc_energy_kwh:.12f}",
                    f"{route.peak_junction_temperature_c:.9f}",
                    f"{route.total_relative_damage:.12e}",
                    f"{route.normalized_time:.9f}",
                    f"{route.normalized_energy:.9f}",
                    f"{route.normalized_damage:.9f}",
                    ""
                    if route.weighted_score is None
                    else f"{route.weighted_score:.9f}",
                    route.rejection_reason or "",
                    str(route.summary_path),
                    str(route.source_cycle),
                ]
            )

    return path


def write_comparison_json(
    result: RouteComparisonResult,
    output_path: str | Path,
) -> Path:
    path = Path(output_path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "time_weight": result.time_weight,
        "energy_weight": result.energy_weight,
        "damage_weight": result.damage_weight,
        "peak_tj_limit_c": result.peak_tj_limit_c,
        "best_route_name": result.best_route_name,
        "routes": [],
    }

    for route in result.routes:
        row = asdict(route)
        row["summary_path"] = str(
            route.summary_path
        )
        row["source_cycle"] = str(
            route.source_cycle
        )
        payload["routes"].append(
            row
        )

    with path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            payload,
            handle,
            indent=2,
        )

    return path
