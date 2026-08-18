from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path

from drive_cycles.thermal_model import (
    run_thermal_analysis,
)


@dataclass(frozen=True)
class RainflowCycle:
    delta_tj_c: float
    mean_tj_c: float
    count: float
    minimum_tj_c: float
    maximum_tj_c: float
    start_time_s: float = 0.0
    end_time_s: float = 0.0
    duration_s: float = 0.0


@dataclass(frozen=True)
class RainflowResult:
    cycles: tuple[RainflowCycle, ...]
    source_cycle: Path
    temperature_sample_count: int
    turning_point_count: int
    minimum_tj_c: float
    maximum_tj_c: float
    equivalent_full_cycles: float
    maximum_delta_tj_c: float
    weighted_mean_delta_tj_c: float
    maximum_cycle_duration_s: float = 0.0
    weighted_mean_cycle_duration_s: float = 0.0


def extract_turning_points(
    values,
    times_s=None,
):
    temperatures = [
        float(value)
        for value in values
    ]

    if times_s is None:
        times = [
            float(index)
            for index in range(
                len(temperatures)
            )
        ]
    else:
        times = [
            float(value)
            for value in times_s
        ]

    if len(times) != len(temperatures):
        raise ValueError(
            "Temperature and time arrays must have the same length."
        )

    data = []

    for time_s, value in zip(
        times,
        temperatures,
    ):
        if (
            not data
            or value != data[-1][1]
        ):
            data.append(
                (time_s, value)
            )
        else:
            # Keep the latest timestamp for a flat plateau.
            data[-1] = (
                time_s,
                value,
            )

    if len(data) <= 2:
        return data

    points = [
        data[0]
    ]

    for i in range(
        1,
        len(data) - 1,
    ):
        a = data[i - 1][1]
        b = data[i][1]
        c = data[i + 1][1]

        if (
            (b - a)
            * (c - b)
            < 0.0
        ):
            points.append(
                data[i]
            )

    points.append(
        data[-1]
    )

    return points


def _cycle_from_points(
    first,
    second,
    count,
):
    t0, a = first
    t1, b = second

    return RainflowCycle(
        delta_tj_c=abs(
            b - a
        ),
        mean_tj_c=0.5 * (
            a + b
        ),
        count=float(
            count
        ),
        minimum_tj_c=min(
            a,
            b,
        ),
        maximum_tj_c=max(
            a,
            b,
        ),
        start_time_s=min(
            t0,
            t1,
        ),
        end_time_s=max(
            t0,
            t1,
        ),
        duration_s=abs(
            t1 - t0
        ),
    )


def count_rainflow_cycles(
    turning_points,
):
    stack = []
    cycles = []

    for point in turning_points:
        stack.append(
            (
                float(point[0]),
                float(point[1]),
            )
        )

        while len(stack) >= 3:
            x = abs(
                stack[-2][1]
                - stack[-3][1]
            )
            y = abs(
                stack[-1][1]
                - stack[-2][1]
            )

            if y < x:
                break

            if len(stack) == 3:
                cycles.append(
                    _cycle_from_points(
                        stack[-3],
                        stack[-2],
                        0.5,
                    )
                )
                del stack[-3]
            else:
                cycles.append(
                    _cycle_from_points(
                        stack[-3],
                        stack[-2],
                        1.0,
                    )
                )
                retained = stack[-1]
                del stack[-3:]
                stack.append(
                    retained
                )

    for i in range(
        len(stack) - 1
    ):
        cycle = _cycle_from_points(
            stack[i],
            stack[i + 1],
            0.5,
        )

        if cycle.delta_tj_c > 0.0:
            cycles.append(
                cycle
            )

    return tuple(
        cycle
        for cycle in cycles
        if cycle.delta_tj_c > 0.0
    )


def analyze_temperature_history(
    temperatures_c,
    *,
    source_cycle,
    times_s=None,
):
    temperatures = [
        float(x)
        for x in temperatures_c
    ]

    if len(temperatures) < 2:
        raise ValueError(
            "At least two temperature samples are required."
        )

    turning_points = extract_turning_points(
        temperatures,
        times_s=times_s,
    )

    cycles = count_rainflow_cycles(
        turning_points
    )

    equivalent = sum(
        cycle.count
        for cycle in cycles
    )

    maximum_delta = max(
        (
            cycle.delta_tj_c
            for cycle in cycles
        ),
        default=0.0,
    )

    weighted_mean = (
        sum(
            cycle.delta_tj_c
            * cycle.count
            for cycle in cycles
        )
        / equivalent
        if equivalent > 0.0
        else 0.0
    )

    maximum_duration = max(
        (
            cycle.duration_s
            for cycle in cycles
        ),
        default=0.0,
    )

    weighted_duration = (
        sum(
            cycle.duration_s
            * cycle.count
            for cycle in cycles
        )
        / equivalent
        if equivalent > 0.0
        else 0.0
    )

    return RainflowResult(
        cycles=cycles,
        source_cycle=Path(
            source_cycle
        ),
        temperature_sample_count=len(
            temperatures
        ),
        turning_point_count=len(
            turning_points
        ),
        minimum_tj_c=min(
            temperatures
        ),
        maximum_tj_c=max(
            temperatures
        ),
        equivalent_full_cycles=equivalent,
        maximum_delta_tj_c=maximum_delta,
        weighted_mean_delta_tj_c=weighted_mean,
        maximum_cycle_duration_s=maximum_duration,
        weighted_mean_cycle_duration_s=weighted_duration,
    )


def write_rainflow_csv(
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
            f"# temperature_sample_count={result.temperature_sample_count}\n"
        )
        handle.write(
            f"# turning_point_count={result.turning_point_count}\n"
        )
        handle.write(
            f"# minimum_tj_c={result.minimum_tj_c:.9f}\n"
        )
        handle.write(
            f"# maximum_tj_c={result.maximum_tj_c:.9f}\n"
        )
        handle.write(
            f"# equivalent_full_cycles={result.equivalent_full_cycles:.9f}\n"
        )
        handle.write(
            f"# maximum_delta_tj_c={result.maximum_delta_tj_c:.9f}\n"
        )
        handle.write(
            f"# weighted_mean_delta_tj_c={result.weighted_mean_delta_tj_c:.9f}\n"
        )
        handle.write(
            f"# maximum_cycle_duration_s={result.maximum_cycle_duration_s:.9f}\n"
        )
        handle.write(
            f"# weighted_mean_cycle_duration_s={result.weighted_mean_cycle_duration_s:.9f}\n"
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
                "minimum_tj_c",
                "maximum_tj_c",
                "start_time_s",
                "end_time_s",
                "duration_s",
            ]
        )

        for index, cycle in enumerate(
            result.cycles,
            start=1,
        ):
            writer.writerow(
                [
                    index,
                    f"{cycle.delta_tj_c:.9f}",
                    f"{cycle.mean_tj_c:.9f}",
                    f"{cycle.count:.1f}",
                    f"{cycle.minimum_tj_c:.9f}",
                    f"{cycle.maximum_tj_c:.9f}",
                    f"{cycle.start_time_s:.9f}",
                    f"{cycle.end_time_s:.9f}",
                    f"{cycle.duration_s:.9f}",
                ]
            )

    return path


def run_rainflow_analysis(
    drive_cycle_path,
    vehicle_config_path,
    *,
    output_path=None,
):
    thermal_result, _ = (
        run_thermal_analysis(
            drive_cycle_path,
            vehicle_config_path,
        )
    )

    result = (
        analyze_temperature_history(
            [
                sample.junction_temperature_c
                for sample
                in thermal_result.samples
            ],
            times_s=[
                sample.time_s
                for sample
                in thermal_result.samples
            ],
            source_cycle=(
                thermal_result.source_cycle
            ),
        )
    )

    source = Path(
        drive_cycle_path
    )

    if output_path is None:
        output_path = (
            source.with_name(
                source.stem
                + "_rainflow.csv"
            )
        )

    return (
        result,
        write_rainflow_csv(
            result,
            output_path,
        ),
    )
