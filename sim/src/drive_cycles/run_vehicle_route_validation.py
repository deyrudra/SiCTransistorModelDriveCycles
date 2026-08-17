from __future__ import annotations

import argparse
from pathlib import Path
import sys

SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))

from drive_cycles.vehicle_route_validation import (
    WLTC_CLASS3_DURATION_S,
    WLTC_CLASS3_DISTANCE_KM,
    WLTC_CLASS3_AVERAGE_SPEED_KMH,
    WLTC_CLASS3_MAX_SPEED_KMH,
    WLTC_CLASS3_STOPPED_TIME_PERCENT,
    validate_vehicle_route,
    save_vehicle_route_validation,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate vehicle/route energy and speed behaviour "
            "against the benchmark stored in the vehicle YAML."
        )
    )

    parser.add_argument(
        "cycle",
        type=Path,
    )

    parser.add_argument(
        "--vehicle-config",
        type=Path,
        required=True,
    )

    args = parser.parse_args()

    result = validate_vehicle_route(
        args.cycle,
        args.vehicle_config,
    )

    csv_path, json_path = (
        save_vehicle_route_validation(
            result
        )
    )

    print()
    print("VEHICLE / ROUTE VALIDATION")
    print("=" * 64)
    print(f"Profile:      {result.profile_name}")
    print(f"Vehicle:      {result.vehicle_name}")
    print()
    print("MISSION SPEED BEHAVIOUR")
    print(f"Duration:     {result.duration_s:.2f} s")
    print(f"Distance:     {result.distance_km:.4f} km")
    print(f"Average:      {result.average_speed_kmh:.2f} km/h")
    print(f"Peak:         {result.peak_speed_kmh:.2f} km/h")
    print(f"Stopped:      {result.stopped_time_percent:.2f} %")
    print(
        f"Accel mean:   {result.mean_positive_accel_mps2:.3f} m/s2"
    )
    print(
        f"Accel p95:    {result.p95_positive_accel_mps2:.3f} m/s2"
    )
    print(
        f"Brake mean:   {result.mean_braking_mps2:.3f} m/s2"
    )
    print(
        f"Brake p95:    {result.p95_braking_magnitude_mps2:.3f} m/s2"
    )

    print()
    print("WLTC CLASS 3 CONTEXT")
    print(f"Reference duration: {WLTC_CLASS3_DURATION_S:.0f} s")
    print(f"Reference distance: {WLTC_CLASS3_DISTANCE_KM:.3f} km")
    print(
        f"Reference average:  {WLTC_CLASS3_AVERAGE_SPEED_KMH:.1f} km/h"
    )
    print(
        f"Reference maximum:  {WLTC_CLASS3_MAX_SPEED_KMH:.1f} km/h"
    )
    print(
        f"Reference stopped:  {WLTC_CLASS3_STOPPED_TIME_PERCENT:.1f} %"
    )
    print(
        f"Average speed delta: {result.wltc_average_speed_delta_kmh:+.2f} km/h"
    )
    print(
        f"Peak speed delta:    {result.wltc_peak_speed_delta_kmh:+.2f} km/h"
    )
    print(
        f"Stopped-time delta:  {result.wltc_stopped_time_delta_percent:+.2f} %-pt"
    )

    print()
    print("ENERGY")
    print(
        f"Traction:      {result.traction_energy_kwh:.6f} kWh"
    )
    print(
        f"Recovered:     {result.recovered_energy_kwh:.6f} kWh"
    )
    print(
        f"Net DC:        {result.net_dc_energy_kwh:.6f} kWh"
    )
    print(
        f"Regen/traction:{result.recovered_fraction_percent:8.2f} %"
    )

    print()
    print("GERMAN VEHICLE BENCHMARK")
    print(f"Benchmark:     {result.benchmark_name}")
    print(f"Cycle:         {result.benchmark_cycle}")
    print(
        f"Official:      {result.benchmark_wh_per_km:.2f} Wh/km"
    )
    print(
        f"Simulation:    {result.simulated_wh_per_km:.2f} Wh/km"
    )
    print(
        f"Difference:    {result.energy_error_wh_per_km:+.2f} Wh/km"
    )
    print(
        f"Error:         {result.energy_error_percent:+.2f} %"
    )
    print(
        f"Assessment:    {result.energy_assessment}"
    )

    print()
    print("NOTE")
    print(result.comparison_scope)
    print()
    print(f"CSV:  {csv_path}")
    print(f"JSON: {json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
