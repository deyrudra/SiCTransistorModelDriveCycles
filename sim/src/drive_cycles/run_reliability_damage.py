from __future__ import annotations

import argparse
from pathlib import Path
import sys


SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))


from drive_cycles.reliability_damage import (
    run_reliability_analysis,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate relative SiC thermal-cycling damage "
            "for a Stuttgart drive cycle."
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
        "--output",
        type=Path,
        default=None,
        help=(
            "Optional output path. Default: "
            "<drive_cycle>_relative_damage.csv"
        ),
    )

    args = parser.parse_args()

    result, output = run_reliability_analysis(
        args.drive_cycle,
        args.vehicle_config,
        output_path=args.output,
    )

    print("Reliability analysis: OK")
    print(f"Input: {result.source_cycle}")
    print(f"Output: {output}")
    print(f"Model: {result.model}")
    print(f"Calibrated: {result.calibrated}")

    if result.calibration_source:
        print(
            f"Calibration source: "
            f"{result.calibration_source}"
        )

    print(
        f"Counted damage cycles: "
        f"{len(result.cycles)}"
    )

    print(
        f"Equivalent full cycles: "
        f"{result.equivalent_full_cycles:.2f}"
    )

    print(
        f"Total relative damage: "
        f"{result.total_relative_damage:.6e}"
    )

    print(
        f"Maximum single-cycle damage contribution: "
        f"{result.maximum_damage_contribution:.6e}"
    )

    print(
        f"Most damaging cycle index: "
        f"{result.most_damaging_cycle_index}"
    )

    print(
        f"Damage-weighted Delta Tj: "
        f"{result.damage_weighted_delta_tj_c:.3f} C"
    )

    print(
        f"Damage-weighted mean Tj: "
        f"{result.damage_weighted_mean_tj_c:.3f} C"
    )

    if not result.calibrated:
        print(
            "NOTE: reliability coefficients are not calibrated; "
            "use total_relative_damage for route-to-route comparison, "
            "not absolute lifetime prediction."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
