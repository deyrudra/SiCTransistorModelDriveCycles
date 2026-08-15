from __future__ import annotations

"""
Validation and mission-profile construction for recorded Stuttgart drive cycles.

Raw recorder schema:
    time_s,v_mps,grade_deg

This module:
- parses metadata comments;
- validates required columns and finite numeric values;
- enforces strictly increasing timestamps;
- checks near-uniform sampling;
- validates non-negative speeds and plausible grades;
- derives acceleration from the speed trace;
- optionally clips acceleration to physical bounds.

It intentionally does not perform inverter or thermal calculations.
"""

from dataclasses import dataclass
import csv
import math
from pathlib import Path
from statistics import median
from typing import Optional


REQUIRED_COLUMNS = (
    "time_s",
    "v_mps",
    "grade_deg",
)


@dataclass(frozen=True)
class DriveCycle:
    time_s: tuple[float, ...]
    speed_mps: tuple[float, ...]
    grade_deg: tuple[float, ...]
    dt_s: float
    metadata: dict[str, str]
    source_path: Path


@dataclass(frozen=True)
class RecordedMissionProfile:
    time_s: tuple[float, ...]
    speed_mps: tuple[float, ...]
    acceleration_mps2: tuple[float, ...]
    grade_deg: tuple[float, ...]
    dt_s: float
    metadata: dict[str, str]
    source_path: Path
    raw_acceleration_min_mps2: float
    raw_acceleration_max_mps2: float
    clipped_acceleration_samples: int


class DriveCycleValidationError(ValueError):
    pass


def _parse_metadata_line(line: str) -> Optional[tuple[str, str]]:
    text = line.lstrip("#").strip()

    if not text or "=" not in text:
        return None

    key, value = text.split("=", 1)

    key = key.strip()
    value = value.strip()

    if not key:
        return None

    return key, value


def load_drive_cycle(
    path: str | Path,
    *,
    max_abs_grade_deg: float = 25.0,
    dt_relative_tolerance: float = 0.05,
    dt_absolute_tolerance_s: float = 1e-6,
) -> DriveCycle:
    source = Path(path).expanduser().resolve()

    if not source.is_file():
        raise FileNotFoundError(
            f"Drive-cycle file not found: {source}"
        )

    metadata: dict[str, str] = {}
    data_lines: list[str] = []

    with source.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        for raw_line in handle:
            if raw_line.lstrip().startswith("#"):
                parsed = _parse_metadata_line(raw_line)

                if parsed is not None:
                    key, value = parsed
                    metadata[key] = value

                continue

            if raw_line.strip():
                data_lines.append(raw_line)

    if not data_lines:
        raise DriveCycleValidationError(
            f"No CSV data found in {source}"
        )

    reader = csv.DictReader(data_lines)

    if reader.fieldnames is None:
        raise DriveCycleValidationError(
            "CSV header is missing."
        )

    missing = [
        name
        for name in REQUIRED_COLUMNS
        if name not in reader.fieldnames
    ]

    if missing:
        raise DriveCycleValidationError(
            "Missing required column(s): "
            + ", ".join(missing)
        )

    times: list[float] = []
    speeds: list[float] = []
    grades: list[float] = []

    for row_number, row in enumerate(
        reader,
        start=2,
    ):
        try:
            t = float(row["time_s"])
            v = float(row["v_mps"])
            grade = float(row["grade_deg"])
        except (TypeError, ValueError) as exc:
            raise DriveCycleValidationError(
                f"Row {row_number}: non-numeric drive-cycle value."
            ) from exc

        if not (
            math.isfinite(t)
            and math.isfinite(v)
            and math.isfinite(grade)
        ):
            raise DriveCycleValidationError(
                f"Row {row_number}: values must be finite."
            )

        if v < 0.0:
            raise DriveCycleValidationError(
                f"Row {row_number}: speed cannot be negative ({v})."
            )

        if abs(grade) > max_abs_grade_deg:
            raise DriveCycleValidationError(
                f"Row {row_number}: grade {grade:.3f} deg exceeds "
                f"configured plausible limit of +/-{max_abs_grade_deg:.1f} deg."
            )

        times.append(t)
        speeds.append(v)
        grades.append(grade)

    if len(times) < 2:
        raise DriveCycleValidationError(
            "Drive cycle requires at least two data samples."
        )

    if times[0] < -dt_absolute_tolerance_s:
        raise DriveCycleValidationError(
            f"First timestamp must be >= 0, got {times[0]}."
        )

    intervals: list[float] = []

    for index in range(1, len(times)):
        dt = times[index] - times[index - 1]

        if dt <= 0.0:
            raise DriveCycleValidationError(
                f"Timestamps must be strictly increasing; "
                f"sample {index} has dt={dt}."
            )

        intervals.append(dt)

    nominal_dt = median(intervals)

    if nominal_dt <= 0.0:
        raise DriveCycleValidationError(
            "Could not determine a valid timestep."
        )

    allowed_dt_error = max(
        dt_absolute_tolerance_s,
        nominal_dt * dt_relative_tolerance,
    )

    worst_dt_error = max(
        abs(dt - nominal_dt)
        for dt in intervals
    )

    if worst_dt_error > allowed_dt_error:
        raise DriveCycleValidationError(
            f"Sampling is not near-uniform: nominal dt={nominal_dt:.6f}s, "
            f"worst deviation={worst_dt_error:.6f}s, "
            f"allowed={allowed_dt_error:.6f}s."
        )

    return DriveCycle(
        time_s=tuple(times),
        speed_mps=tuple(speeds),
        grade_deg=tuple(grades),
        dt_s=float(nominal_dt),
        metadata=metadata,
        source_path=source,
    )


def _differentiate_speed(
    time_s: tuple[float, ...],
    speed_mps: tuple[float, ...],
) -> list[float]:
    count = len(time_s)

    acceleration = [0.0] * count

    if count == 2:
        dt = time_s[1] - time_s[0]
        value = (
            speed_mps[1] - speed_mps[0]
        ) / dt

        acceleration[0] = value
        acceleration[1] = value
        return acceleration

    # One-sided derivative at start.
    acceleration[0] = (
        speed_mps[1] - speed_mps[0]
    ) / (
        time_s[1] - time_s[0]
    )

    # Central difference through the interior.
    for index in range(1, count - 1):
        dt = (
            time_s[index + 1]
            - time_s[index - 1]
        )

        acceleration[index] = (
            speed_mps[index + 1]
            - speed_mps[index - 1]
        ) / dt

    # One-sided derivative at end.
    acceleration[-1] = (
        speed_mps[-1] - speed_mps[-2]
    ) / (
        time_s[-1] - time_s[-2]
    )

    return acceleration


def build_mission_profile(
    drive_cycle: DriveCycle,
    *,
    min_acceleration_mps2: float = -8.0,
    max_acceleration_mps2: float = 3.0,
) -> RecordedMissionProfile:
    """
    Derive acceleration from the validated speed trace.

    Clipping is diagnostic protection against route-completion discontinuities
    or corrupted data. A clean Stuttgart run should require little or no
    clipping.
    """

    if min_acceleration_mps2 >= max_acceleration_mps2:
        raise ValueError(
            "Minimum acceleration must be less than maximum acceleration."
        )

    raw = _differentiate_speed(
        drive_cycle.time_s,
        drive_cycle.speed_mps,
    )

    raw_min = min(raw)
    raw_max = max(raw)

    clipped: list[float] = []
    clipped_count = 0

    for value in raw:
        bounded = min(
            max_acceleration_mps2,
            max(
                min_acceleration_mps2,
                value,
            ),
        )

        if not math.isclose(
            bounded,
            value,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            clipped_count += 1

        clipped.append(bounded)

    metadata = dict(drive_cycle.metadata)
    metadata["acceleration_clip_min_mps2"] = str(
        min_acceleration_mps2
    )
    metadata["acceleration_clip_max_mps2"] = str(
        max_acceleration_mps2
    )
    metadata["acceleration_clipped_samples"] = str(
        clipped_count
    )

    return RecordedMissionProfile(
        time_s=drive_cycle.time_s,
        speed_mps=drive_cycle.speed_mps,
        acceleration_mps2=tuple(clipped),
        grade_deg=drive_cycle.grade_deg,
        dt_s=drive_cycle.dt_s,
        metadata=metadata,
        source_path=drive_cycle.source_path,
        raw_acceleration_min_mps2=raw_min,
        raw_acceleration_max_mps2=raw_max,
        clipped_acceleration_samples=clipped_count,
    )


def summarize_drive_cycle(
    cycle: DriveCycle,
) -> dict[str, float | int | str]:
    duration = (
        cycle.time_s[-1] - cycle.time_s[0]
    )

    max_speed = max(cycle.speed_mps)
    min_grade = min(cycle.grade_deg)
    max_grade = max(cycle.grade_deg)

    distance_m = 0.0

    for i in range(1, len(cycle.time_s)):
        dt = (
            cycle.time_s[i]
            - cycle.time_s[i - 1]
        )

        distance_m += (
            0.5
            * (
                cycle.speed_mps[i]
                + cycle.speed_mps[i - 1]
            )
            * dt
        )

    return {
        "source": str(cycle.source_path),
        "samples": len(cycle.time_s),
        "duration_s": duration,
        "dt_s": cycle.dt_s,
        "distance_km": distance_m / 1000.0,
        "max_speed_kmh": max_speed * 3.6,
        "min_grade_deg": min_grade,
        "max_grade_deg": max_grade,
    }
