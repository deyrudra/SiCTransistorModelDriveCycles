from __future__ import annotations

import argparse
from pathlib import Path
import sys

SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))

from drive_cycles.thermal_model import run_thermal_analysis


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("drive_cycle", type=Path)
    parser.add_argument("--vehicle-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result, output = run_thermal_analysis(
        args.drive_cycle,
        args.vehicle_config,
        output_path=args.output,
    )

    print("Thermal analysis: OK")
    print(f"Vehicle: {result.vehicle_name}")
    print(f"Input: {result.source_cycle}")
    print(f"Output: {output}")
    print(f"Samples: {len(result.samples)}")
    print(f"Peak phase current: {result.peak_phase_current_a:.2f} A")
    print(f"Peak aggregate semiconductor loss: {result.peak_aggregate_loss_w:.2f} W")
    print(f"Peak representative-device loss: {result.peak_device_loss_w:.2f} W")
    print(f"Peak case temperature: {result.peak_case_temperature_c:.2f} C")
    print(f"Peak junction temperature: {result.peak_junction_temperature_c:.2f} C")
    print(f"Aggregate loss energy: {result.aggregate_loss_energy_j:.2f} J")
    print(f"Non-converged thermal samples: {result.nonconverged_samples}")
    print(f"Samples above configured Tj max: {result.overtemperature_samples}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
