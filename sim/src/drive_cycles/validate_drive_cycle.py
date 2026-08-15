from __future__ import annotations
import sys

"""
Command-line validator for recorded Stuttgart drive cycles.

Usage:
    python -m drive_cycles.validate_drive_cycle sim/cycles/file.csv
"""

import argparse
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))

from drive_cycles.drive_cycle_loader import (
    build_mission_profile,
    load_drive_cycle,
    summarize_drive_cycle,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a recorded Stuttgart drive-cycle CSV."
    )

    parser.add_argument(
        "drive_cycle",
        type=Path,
        help="Path to drive_cycle_*.csv",
    )

    parser.add_argument(
        "--min-accel",
        type=float,
        default=-8.0,
        help="Minimum allowed derived acceleration in m/s^2.",
    )

    parser.add_argument(
        "--max-accel",
        type=float,
        default=3.0,
        help="Maximum allowed derived acceleration in m/s^2.",
    )

    args = parser.parse_args()

    cycle = load_drive_cycle(
        args.drive_cycle
    )

    profile = build_mission_profile(
        cycle,
        min_acceleration_mps2=args.min_accel,
        max_acceleration_mps2=args.max_accel,
    )

    summary = summarize_drive_cycle(
        cycle
    )

    print("Drive-cycle validation: OK")
    print(f"File: {summary['source']}")
    print(f"Samples: {summary['samples']}")
    print(f"Duration: {summary['duration_s']:.2f} s")
    print(f"Nominal dt: {summary['dt_s']:.6f} s")
    print(f"Distance: {summary['distance_km']:.3f} km")
    print(f"Peak speed: {summary['max_speed_kmh']:.2f} km/h")
    print(
        f"Grade range: "
        f"{summary['min_grade_deg']:+.3f} to "
        f"{summary['max_grade_deg']:+.3f} deg"
    )
    print(
        f"Raw acceleration range: "
        f"{profile.raw_acceleration_min_mps2:+.3f} to "
        f"{profile.raw_acceleration_max_mps2:+.3f} m/s^2"
    )
    print(
        f"Acceleration samples clipped: "
        f"{profile.clipped_acceleration_samples}"
    )

    if profile.clipped_acceleration_samples:
        print(
            "WARNING: acceleration clipping occurred. "
            "Inspect destination stopping, timestep, or speed discontinuities."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
