from __future__ import annotations

from dataclasses import dataclass
import csv
import math
from pathlib import Path
import shutil
import urllib.request

import yaml

from drive_cycles.longitudinal_profile import run_longitudinal_analysis


UNECE_WLTC_WORKBOOK_URL = (
    "https://unece.org/fileadmin/DAM/trans/doc/2012/wp29grpe/"
    "WLTP-DHC-12-07e.xls"
)

# Aggregate checks used only to verify that the imported trace is genuinely
# consistent with a full Class 3 cycle. They are not used to generate speeds.
EXPECTED_DURATION_S = 1800.0
EXPECTED_DISTANCE_KM = 23.266
EXPECTED_AVERAGE_SPEED_KMH = 46.5
EXPECTED_PEAK_SPEED_KMH = 131.3


@dataclass(frozen=True)
class WltcTraceSummary:
    source_path: str
    sample_count: int
    duration_s: float
    distance_km: float
    average_speed_kmh: float
    peak_speed_kmh: float
    stopped_time_percent: float
    speed_checksum_kmh: float


@dataclass(frozen=True)
class WltcValidationResult:
    trace: WltcTraceSummary
    benchmark_name: str
    benchmark_wh_per_km: float

    traction_energy_kwh: float
    recovered_energy_kwh: float
    base_auxiliary_energy_kwh: float
    hvac_energy_kwh: float
    total_auxiliary_energy_kwh: float
    net_battery_energy_kwh: float

    simulated_wh_per_km: float
    error_wh_per_km: float
    error_percent: float

    trace_duration_error_s: float
    trace_distance_error_km: float
    trace_average_speed_error_kmh: float
    trace_peak_speed_error_kmh: float

    trace_assessment: str
    energy_assessment: str


def download_official_workbook(
    destination: str | Path,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    request = urllib.request.Request(
        UNECE_WLTC_WORKBOOK_URL,
        headers={
            "User-Agent": (
                "SiCTransistorModelDriveCycles/1.0 "
                "(academic vehicle validation)"
            )
        },
    )

    temporary = destination.with_suffix(
        destination.suffix + ".tmp"
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:
        with temporary.open("wb") as handle:
            shutil.copyfileobj(
                response,
                handle,
            )

    temporary.replace(destination)
    return destination


def _numeric(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            return float(value)
    return None


def _find_time_column(sheet):
    """
    Find a 0..1800 second sequence inside the official workbook instead of
    depending on a particular row number.
    """
    required_checks = (
        (0, 0.0),
        (1, 1.0),
        (10, 10.0),
        (100, 100.0),
        (600, 600.0),
        (1200, 1200.0),
        (1800, 1800.0),
    )

    for col in range(sheet.ncols):
        max_start = sheet.nrows - 1801

        for start in range(max_start + 1):
            valid = True

            for offset, expected in required_checks:
                value = _numeric(
                    sheet.cell_value(
                        start + offset,
                        col,
                    )
                )

                if (
                    value is None
                    or abs(value - expected) > 1e-6
                ):
                    valid = False
                    break

            if valid:
                return start, col

    raise ValueError(
        "Could not find a complete 0..1800 s WLTC time column "
        "in the selected workbook."
    )


def _find_speed_column(
    sheet,
    start_row: int,
    time_col: int,
):
    best = None

    for col in range(sheet.ncols):
        if col == time_col:
            continue

        values = []

        valid = True
        for offset in range(1801):
            value = _numeric(
                sheet.cell_value(
                    start_row + offset,
                    col,
                )
            )

            if value is None:
                valid = False
                break

            values.append(value)

        if not valid:
            continue

        peak = max(values)
        minimum = min(values)

        if minimum < -0.1 or peak > 160.0:
            continue

        # A speed column should have a Class-3-like peak and non-trivial sum.
        checksum = sum(values)

        if not (
            120.0 <= peak <= 140.0
            and 60_000.0 <= checksum <= 100_000.0
        ):
            continue

        score = abs(
            peak - EXPECTED_PEAK_SPEED_KMH
        )

        if best is None or score < best[0]:
            best = (
                score,
                col,
                values,
            )

    if best is None:
        raise ValueError(
            "Found the 0..1800 s time column, but could not identify "
            "the Class 3 target-speed column."
        )

    return best[1], best[2]


def extract_class3_trace_from_xls(
    workbook_path: str | Path,
    output_csv_path: str | Path,
) -> Path:
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError(
            "Reading the official UNECE .xls workbook requires the small "
            "'xlrd' package. Install it once with: python -m pip install xlrd"
        ) from exc

    workbook_path = Path(workbook_path)
    output_csv_path = Path(output_csv_path)

    workbook = xlrd.open_workbook(
        str(workbook_path)
    )

    candidate_names = [
        name
        for name in workbook.sheet_names()
        if "WLTC_class_3" in name
        or "WLTC class 3" in name.lower()
    ]

    if candidate_names:
        sheet = workbook.sheet_by_name(
            candidate_names[0]
        )
    else:
        # Fall back to searching every worksheet.
        sheet = None

        for index in range(workbook.nsheets):
            candidate = workbook.sheet_by_index(index)
            try:
                _find_time_column(candidate)
            except ValueError:
                continue
            sheet = candidate
            break

        if sheet is None:
            raise ValueError(
                "No worksheet containing a full Class 3 trace was found."
            )

    start_row, time_col = _find_time_column(
        sheet
    )

    _, speed_values_kmh = _find_speed_column(
        sheet,
        start_row,
        time_col,
    )

    output_csv_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_csv_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        handle.write(
            "# source=UNECE WLTP-DHC-12-07e official workbook\n"
        )
        handle.write(
            f"# source_workbook={workbook_path.resolve()}\n"
        )
        handle.write(
            "# cycle=WLTC Class 3\n"
        )
        handle.write(
            "# test_mode=prescribed_speed_flat_grade\n"
        )

        writer = csv.writer(handle)
        writer.writerow(
            [
                "time_s",
                "v_mps",
                "grade_deg",
            ]
        )

        for second, speed_kmh in enumerate(
            speed_values_kmh
        ):
            writer.writerow(
                [
                    f"{float(second):.1f}",
                    f"{speed_kmh / 3.6:.9f}",
                    "0.0",
                ]
            )

    validate_trace_file(
        output_csv_path,
        strict=True,
    )

    return output_csv_path


def _read_trace(
    path: str | Path,
):
    times = []
    speeds_mps = []

    with Path(path).open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        lines = (
            line
            for line in handle
            if not line.lstrip().startswith("#")
        )

        reader = csv.DictReader(lines)

        for row in reader:
            times.append(
                float(row["time_s"])
            )
            speeds_mps.append(
                float(row["v_mps"])
            )

    if len(times) < 2:
        raise ValueError(
            "WLTC trace must contain at least two samples."
        )

    return times, speeds_mps


def summarize_trace(
    path: str | Path,
) -> WltcTraceSummary:
    times, speeds = _read_trace(path)

    distance_m = 0.0
    stopped_s = 0.0
    total_s = 0.0

    for index in range(1, len(times)):
        dt = (
            times[index]
            - times[index - 1]
        )

        if dt <= 0.0:
            raise ValueError(
                "WLTC trace time values must be strictly increasing."
            )

        v0 = speeds[index - 1]
        v1 = speeds[index]

        distance_m += (
            0.5
            * (v0 + v1)
            * dt
        )

        total_s += dt

        if (
            0.5 * (v0 + v1)
            < (1.0 / 3.6)
        ):
            stopped_s += dt

    duration_s = (
        times[-1]
        - times[0]
    )

    distance_km = (
        distance_m
        / 1000.0
    )

    average_speed_kmh = (
        distance_km
        / (duration_s / 3600.0)
        if duration_s > 0.0
        else 0.0
    )

    return WltcTraceSummary(
        source_path=str(
            Path(path).resolve()
        ),
        sample_count=len(times),
        duration_s=duration_s,
        distance_km=distance_km,
        average_speed_kmh=average_speed_kmh,
        peak_speed_kmh=max(speeds) * 3.6,
        stopped_time_percent=(
            100.0
            * stopped_s
            / total_s
            if total_s > 0.0
            else 0.0
        ),
        speed_checksum_kmh=sum(
            speed * 3.6
            for speed in speeds
        ),
    )


def validate_trace_file(
    path: str | Path,
    *,
    strict: bool,
) -> WltcTraceSummary:
    summary = summarize_trace(path)

    problems = []

    if abs(
        summary.duration_s
        - EXPECTED_DURATION_S
    ) > 1.0:
        problems.append(
            f"duration {summary.duration_s:.1f} s"
        )

    if abs(
        summary.distance_km
        - EXPECTED_DISTANCE_KM
    ) > 0.35:
        problems.append(
            f"distance {summary.distance_km:.3f} km"
        )

    if abs(
        summary.average_speed_kmh
        - EXPECTED_AVERAGE_SPEED_KMH
    ) > 1.0:
        problems.append(
            f"average speed {summary.average_speed_kmh:.2f} km/h"
        )

    if abs(
        summary.peak_speed_kmh
        - EXPECTED_PEAK_SPEED_KMH
    ) > 1.0:
        problems.append(
            f"peak speed {summary.peak_speed_kmh:.2f} km/h"
        )

    if strict and problems:
        raise ValueError(
            "Imported trace does not look like a complete WLTC Class 3 "
            "cycle: "
            + ", ".join(problems)
        )

    return summary


def _benchmark_from_yaml(
    vehicle_config_path: str | Path,
):
    with Path(vehicle_config_path).open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = yaml.safe_load(
            handle
        ) or {}

    validation = data.get(
        "validation",
        {},
    )

    wh_per_km = validation.get(
        "official_consumption_wh_per_km"
    )

    if wh_per_km is None:
        kwh_per_100km = validation.get(
            "official_consumption_kwh_per_100km"
        )

        if kwh_per_100km is not None:
            wh_per_km = (
                float(kwh_per_100km)
                * 10.0
            )

    if wh_per_km is None:
        raise ValueError(
            "Vehicle YAML does not contain a WLTP consumption benchmark."
        )

    return (
        str(
            validation.get(
                "benchmark_name",
                data.get(
                    "vehicle",
                    {},
                ).get(
                    "name",
                    "vehicle benchmark",
                ),
            )
        ),
        float(wh_per_km),
    )


def run_wltc_validation(
    trace_csv_path: str | Path,
    vehicle_config_path: str | Path,
) -> WltcValidationResult:
    trace = validate_trace_file(
        trace_csv_path,
        strict=True,
    )

    longitudinal, _ = run_longitudinal_analysis(
        trace_csv_path,
        vehicle_config_path,
        output_path=(
            Path(trace_csv_path)
            .with_name(
                Path(trace_csv_path).stem
                + "_vehicle_power.csv"
            )
        ),
    )

    benchmark_name, benchmark_wh_per_km = (
        _benchmark_from_yaml(
            vehicle_config_path
        )
    )

    simulated_wh_per_km = (
        longitudinal.net_battery_energy_kwh
        * 1000.0
        / trace.distance_km
    )

    error_wh_per_km = (
        simulated_wh_per_km
        - benchmark_wh_per_km
    )

    error_percent = (
        100.0
        * error_wh_per_km
        / benchmark_wh_per_km
    )

    trace_errors = (
        abs(trace.duration_s - EXPECTED_DURATION_S),
        abs(trace.distance_km - EXPECTED_DISTANCE_KM),
        abs(
            trace.average_speed_kmh
            - EXPECTED_AVERAGE_SPEED_KMH
        ),
        abs(
            trace.peak_speed_kmh
            - EXPECTED_PEAK_SPEED_KMH
        ),
    )

    trace_assessment = (
        "PASS"
        if (
            trace_errors[0] <= 1.0
            and trace_errors[1] <= 0.35
            and trace_errors[2] <= 1.0
            and trace_errors[3] <= 1.0
        )
        else "REVIEW_TRACE"
    )

    magnitude = abs(
        error_percent
    )

    if magnitude <= 10.0:
        energy_assessment = "GOOD_MATCH"
    elif magnitude <= 20.0:
        energy_assessment = "REVIEW_MODEL"
    else:
        energy_assessment = "LARGE_MISMATCH"

    return WltcValidationResult(
        trace=trace,
        benchmark_name=benchmark_name,
        benchmark_wh_per_km=benchmark_wh_per_km,
        traction_energy_kwh=longitudinal.traction_energy_kwh,
        recovered_energy_kwh=longitudinal.recovered_energy_kwh,
        base_auxiliary_energy_kwh=longitudinal.base_auxiliary_energy_kwh,
        hvac_energy_kwh=longitudinal.hvac_energy_kwh,
        total_auxiliary_energy_kwh=longitudinal.total_auxiliary_energy_kwh,
        net_battery_energy_kwh=longitudinal.net_battery_energy_kwh,
        simulated_wh_per_km=simulated_wh_per_km,
        error_wh_per_km=error_wh_per_km,
        error_percent=error_percent,
        trace_duration_error_s=(
            trace.duration_s
            - EXPECTED_DURATION_S
        ),
        trace_distance_error_km=(
            trace.distance_km
            - EXPECTED_DISTANCE_KM
        ),
        trace_average_speed_error_kmh=(
            trace.average_speed_kmh
            - EXPECTED_AVERAGE_SPEED_KMH
        ),
        trace_peak_speed_error_kmh=(
            trace.peak_speed_kmh
            - EXPECTED_PEAK_SPEED_KMH
        ),
        trace_assessment=trace_assessment,
        energy_assessment=energy_assessment,
    )
