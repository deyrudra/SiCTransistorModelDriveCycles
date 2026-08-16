from __future__ import annotations

import argparse
from pathlib import Path
import sys

SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))

from drive_cycles.inverter_electrical import (
    run_inverter_analysis,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run inverter current and semiconductor-loss analysis "
            "for a recorded Stuttgart drive cycle."
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
            "<drive_cycle>_inverter_losses.csv"
        ),
    )

    args = parser.parse_args()

    result, output = run_inverter_analysis(
        args.drive_cycle,
        args.vehicle_config,
        output_path=args.output,
    )

    print("Inverter analysis: OK")
    print(f"Vehicle: {result.vehicle_name}")
    print(f"Input: {result.source_cycle}")
    print(f"Output: {output}")
    print(f"Samples: {len(result.samples)}")
    print(
        f"Peak phase current: "
        f"{result.peak_phase_current_a:.2f} A"
    )
    print(
        f"Peak device current: "
        f"{result.peak_device_current_a:.2f} A"
    )
    print(
        f"Peak semiconductor loss: "
        f"{result.peak_total_loss_w:.2f} W"
    )
    print(
        f"Peak unserved power magnitude: "
        f"{result.peak_unserved_power_w:.2f} W"
    )
    print(
        f"Aggregate semiconductor loss energy: "
        f"{result.loss_energy_wh:.6f} Wh"
    )

    if result.peak_unserved_power_w > 1e-6:
        print(
            "WARNING: inverter current limit prevented the configured "
            "system from serving the full requested DC power."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
