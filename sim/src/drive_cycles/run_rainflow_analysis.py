from __future__ import annotations

import argparse
from pathlib import Path
import sys

SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))

from drive_cycles.rainflow_analysis import run_rainflow_analysis

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run rainflow counting on the junction-temperature history."
    )
    parser.add_argument("drive_cycle", type=Path)
    parser.add_argument("--vehicle-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result, output = run_rainflow_analysis(
        args.drive_cycle,
        args.vehicle_config,
        output_path=args.output,
    )

    print("Rainflow analysis: OK")
    print(f"Input: {result.source_cycle}")
    print(f"Output: {output}")
    print(f"Temperature samples: {result.temperature_sample_count}")
    print(f"Turning points: {result.turning_point_count}")
    print(f"Counted rainflow ranges: {len(result.cycles)}")
    print(f"Equivalent full cycles: {result.equivalent_full_cycles:.2f}")
    print(
        f"Junction-temperature range: "
        f"{result.minimum_tj_c:.2f} to {result.maximum_tj_c:.2f} C"
    )
    print(f"Maximum Delta Tj: {result.maximum_delta_tj_c:.3f} C")
    print(
        f"Cycle-weighted mean Delta Tj: "
        f"{result.weighted_mean_delta_tj_c:.3f} C"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
