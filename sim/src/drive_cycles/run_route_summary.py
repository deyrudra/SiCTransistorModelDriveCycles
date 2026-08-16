from __future__ import annotations

import argparse
from pathlib import Path
import sys

SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))

from drive_cycles.route_summary import (
    run_route_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete Stuttgart drive-cycle analysis "
            "and print one compact route summary."
        )
    )

    parser.add_argument(
        "drive_cycle",
        type=Path,
        help="Path to drive_cycle_*.csv",
    )

    parser.add_argument(
        "--vehicle-config",
        type=Path,
        required=True,
        help="Path to vehicle YAML configuration.",
    )

    parser.add_argument(
        "--csv-output",
        type=Path,
        default=None,
        help=(
            "Optional CSV summary output path. Default: "
            "<drive_cycle>_summary.csv"
        ),
    )

    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help=(
            "Optional JSON summary output path. Default: "
            "<drive_cycle>_summary.json"
        ),
    )

    args = parser.parse_args()

    summary, csv_path, json_path = run_route_summary(
        args.drive_cycle,
        args.vehicle_config,
        csv_output_path=args.csv_output,
        json_output_path=args.json_output,
    )

    print("Route summary: OK")
    print(f"Vehicle: {summary.vehicle_name}")
    print(f"Input: {summary.source_cycle}")
    print(f"CSV summary: {csv_path}")
    print(f"JSON summary: {json_path}")

    print()
    print(f"Duration: {summary.duration_s:.2f} s")
    print(f"Distance: {summary.distance_km:.4f} km")

    print(
        f"Traction energy: "
        f"{summary.traction_energy_kwh:.6f} kWh"
    )

    print(
        f"Recovered energy: "
        f"{summary.recovered_energy_kwh:.6f} kWh"
    )

    print(
        f"Net DC energy: "
        f"{summary.net_dc_energy_kwh:.6f} kWh"
    )

    print(
        f"Peak DC propulsion power: "
        f"{summary.peak_dc_propulsion_power_kw:.2f} kW"
    )

    print(
        f"Peak junction temperature: "
        f"{summary.peak_junction_temperature_c:.2f} C"
    )

    print(
        f"Maximum Delta Tj: "
        f"{summary.maximum_delta_tj_c:.3f} C"
    )

    print(
        f"Equivalent full thermal cycles: "
        f"{summary.equivalent_full_cycles:.2f}"
    )

    print(
        f"Total relative SiC damage: "
        f"{summary.total_relative_damage:.6e}"
    )

    print(
        f"Reliability calibrated: "
        f"{summary.reliability_calibrated}"
    )

    print(
        f"Non-converged thermal samples: "
        f"{summary.nonconverged_thermal_samples}"
    )

    print(
        f"Samples above configured Tj max: "
        f"{summary.overtemperature_samples}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
