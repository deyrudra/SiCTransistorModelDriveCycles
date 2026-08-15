from __future__ import annotations

import argparse
from pathlib import Path
import sys

SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))

from drive_cycles.longitudinal_profile import (
    run_longitudinal_analysis,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run longitudinal vehicle analysis for a recorded Stuttgart "
            "drive cycle."
        )
    )

    parser.add_argument(
        "drive_cycle",
        type=Path,
        help="Path to recorded drive_cycle_*.csv",
    )

    parser.add_argument(
        "--vehicle-config",
        type=Path,
        required=True,
        help="Path to vehicle YAML configuration.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Optional output CSV path. Default: input filename with "
            "_power.csv suffix."
        ),
    )

    args = parser.parse_args()

    result, output_path = run_longitudinal_analysis(
        args.drive_cycle,
        args.vehicle_config,
        output_path=args.output,
    )

    print("Longitudinal analysis: OK")
    print(f"Vehicle: {result.vehicle_name}")
    print(f"Input: {result.source_cycle}")
    print(f"Output: {output_path}")
    print(f"Samples: {len(result.samples)}")
    print(f"Distance: {result.distance_km:.4f} km")
    print(
        f"Traction energy: "
        f"{result.traction_energy_kwh:.6f} kWh"
    )
    print(
        f"Recovered energy: "
        f"{result.recovered_energy_kwh:.6f} kWh"
    )
    print(
        f"Friction-brake energy: "
        f"{result.friction_brake_energy_kwh:.6f} kWh"
    )
    print(
        f"Peak wheel power: "
        f"{result.peak_wheel_power_kw:.2f} kW"
    )
    print(
        f"Peak DC propulsion power: "
        f"{result.peak_dc_power_kw:.2f} kW"
    )
    print(
        f"Peak DC regenerative power: "
        f"{result.peak_regen_power_kw:.2f} kW"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
