from __future__ import annotations

import argparse
from pathlib import Path
import sys


SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))
    

from drive_cycles.compare_routes import (
    compare_route_summaries,
    write_comparison_csv,
    write_comparison_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare multiple Stuttgart route-summary JSON files "
            "using normalized time, energy, and relative SiC damage."
        )
    )

    parser.add_argument(
        "summaries",
        nargs="+",
        type=Path,
        help="Two or more *_summary.json files.",
    )

    parser.add_argument(
        "--time-weight",
        type=float,
        default=0.40,
    )

    parser.add_argument(
        "--energy-weight",
        type=float,
        default=0.30,
    )

    parser.add_argument(
        "--damage-weight",
        type=float,
        default=0.30,
    )

    parser.add_argument(
        "--peak-tj-limit-c",
        type=float,
        default=None,
        help=(
            "Optional hard peak-junction-temperature limit. "
            "Routes above the limit are excluded from ranking."
        ),
    )

    parser.add_argument(
        "--csv-output",
        type=Path,
        default=Path("sim/cycles/route_comparison.csv"),
    )

    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("sim/cycles/route_comparison.json"),
    )

    args = parser.parse_args()

    result = compare_route_summaries(
        args.summaries,
        time_weight=args.time_weight,
        energy_weight=args.energy_weight,
        damage_weight=args.damage_weight,
        peak_tj_limit_c=args.peak_tj_limit_c,
    )

    csv_path = write_comparison_csv(
        result,
        args.csv_output,
    )

    json_path = write_comparison_json(
        result,
        args.json_output,
    )

    print("Route comparison: OK")
    print(
        "Normalized weights: "
        f"time={result.time_weight:.3f}, "
        f"energy={result.energy_weight:.3f}, "
        f"damage={result.damage_weight:.3f}"
    )

    if result.peak_tj_limit_c is not None:
        print(
            f"Peak Tj limit: "
            f"{result.peak_tj_limit_c:.2f} C"
        )

    print()
    print(
        f"{'Rank':>4}  "
        f"{'Route':<38} "
        f"{'Time(s)':>9} "
        f"{'Energy(kWh)':>12} "
        f"{'PeakTj(C)':>10} "
        f"{'RelDamage':>12} "
        f"{'Score':>8}"
    )

    print(
        "-" * 102
    )

    for route in result.routes:
        rank = (
            str(route.rank)
            if route.rank is not None
            else "-"
        )

        score = (
            f"{route.weighted_score:.4f}"
            if route.weighted_score is not None
            else "-"
        )

        print(
            f"{rank:>4}  "
            f"{route.route_name:<38.38} "
            f"{route.duration_s:>9.2f} "
            f"{route.net_dc_energy_kwh:>12.6f} "
            f"{route.peak_junction_temperature_c:>10.2f} "
            f"{route.total_relative_damage:>12.3e} "
            f"{score:>8}"
        )

        if route.rejection_reason:
            print(
                f"      rejected: "
                f"{route.rejection_reason}"
            )

    print()
    print(
        f"Best route: "
        f"{result.best_route_name}"
    )

    print(
        f"CSV comparison: "
        f"{csv_path}"
    )

    print(
        f"JSON comparison: "
        f"{json_path}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
