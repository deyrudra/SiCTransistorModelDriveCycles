from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path

from drive_cycles.thermal_model import run_thermal_analysis


@dataclass(frozen=True)
class RainflowCycle:
    delta_tj_c: float
    mean_tj_c: float
    count: float
    minimum_tj_c: float
    maximum_tj_c: float


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


def extract_turning_points(values):
    data = []
    for value in values:
        value = float(value)
        if not data or value != data[-1]:
            data.append(value)

    if len(data) <= 2:
        return data

    points = [data[0]]

    for i in range(1, len(data) - 1):
        a = data[i - 1]
        b = data[i]
        c = data[i + 1]

        if (b - a) * (c - b) < 0.0:
            points.append(b)

    points.append(data[-1])
    return points


def count_rainflow_cycles(turning_points):
    stack = []
    cycles = []

    for point in turning_points:
        stack.append(float(point))

        while len(stack) >= 3:
            x = abs(stack[-2] - stack[-3])
            y = abs(stack[-1] - stack[-2])

            if y < x:
                break

            minimum = min(stack[-3], stack[-2])
            maximum = max(stack[-3], stack[-2])

            if len(stack) == 3:
                cycles.append(
                    RainflowCycle(
                        x,
                        0.5 * (stack[-3] + stack[-2]),
                        0.5,
                        minimum,
                        maximum,
                    )
                )
                del stack[-3]
            else:
                cycles.append(
                    RainflowCycle(
                        x,
                        0.5 * (stack[-3] + stack[-2]),
                        1.0,
                        minimum,
                        maximum,
                    )
                )
                retained = stack[-1]
                del stack[-3:]
                stack.append(retained)

    for i in range(len(stack) - 1):
        a = stack[i]
        b = stack[i + 1]
        delta = abs(b - a)

        if delta > 0.0:
            cycles.append(
                RainflowCycle(
                    delta,
                    0.5 * (a + b),
                    0.5,
                    min(a, b),
                    max(a, b),
                )
            )

    return tuple(c for c in cycles if c.delta_tj_c > 0.0)


def analyze_temperature_history(temperatures_c, *, source_cycle):
    temperatures = [float(x) for x in temperatures_c]

    if len(temperatures) < 2:
        raise ValueError("At least two temperature samples are required.")

    turning_points = extract_turning_points(temperatures)
    cycles = count_rainflow_cycles(turning_points)

    equivalent = sum(c.count for c in cycles)
    maximum_delta = max((c.delta_tj_c for c in cycles), default=0.0)

    weighted_mean = (
        sum(c.delta_tj_c * c.count for c in cycles) / equivalent
        if equivalent > 0.0
        else 0.0
    )

    return RainflowResult(
        cycles=cycles,
        source_cycle=Path(source_cycle),
        temperature_sample_count=len(temperatures),
        turning_point_count=len(turning_points),
        minimum_tj_c=min(temperatures),
        maximum_tj_c=max(temperatures),
        equivalent_full_cycles=equivalent,
        maximum_delta_tj_c=maximum_delta,
        weighted_mean_delta_tj_c=weighted_mean,
    )


def write_rainflow_csv(result, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(f"# source_cycle={result.source_cycle}\n")
        handle.write(f"# temperature_sample_count={result.temperature_sample_count}\n")
        handle.write(f"# turning_point_count={result.turning_point_count}\n")
        handle.write(f"# minimum_tj_c={result.minimum_tj_c:.9f}\n")
        handle.write(f"# maximum_tj_c={result.maximum_tj_c:.9f}\n")
        handle.write(f"# equivalent_full_cycles={result.equivalent_full_cycles:.9f}\n")
        handle.write(f"# maximum_delta_tj_c={result.maximum_delta_tj_c:.9f}\n")
        handle.write(f"# weighted_mean_delta_tj_c={result.weighted_mean_delta_tj_c:.9f}\n")

        writer = csv.writer(handle)
        writer.writerow([
            "cycle_index",
            "delta_tj_c",
            "mean_tj_c",
            "count",
            "minimum_tj_c",
            "maximum_tj_c",
        ])

        for index, cycle in enumerate(result.cycles, start=1):
            writer.writerow([
                index,
                f"{cycle.delta_tj_c:.9f}",
                f"{cycle.mean_tj_c:.9f}",
                f"{cycle.count:.1f}",
                f"{cycle.minimum_tj_c:.9f}",
                f"{cycle.maximum_tj_c:.9f}",
            ])

    return path


def run_rainflow_analysis(
    drive_cycle_path,
    vehicle_config_path,
    *,
    output_path=None,
):
    thermal_result, _ = run_thermal_analysis(
        drive_cycle_path,
        vehicle_config_path,
    )

    result = analyze_temperature_history(
        [s.junction_temperature_c for s in thermal_result.samples],
        source_cycle=thermal_result.source_cycle,
    )

    source = Path(drive_cycle_path)

    if output_path is None:
        output_path = source.with_name(source.stem + "_rainflow.csv")

    return result, write_rainflow_csv(result, output_path)
