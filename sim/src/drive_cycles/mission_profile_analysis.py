from __future__ import annotations

from dataclasses import asdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
import csv
import json
import multiprocessing
import os
from pathlib import Path


DERIVED_SUFFIXES = (
    "_power",
    "_inverter_losses",
    "_thermal_trace",
    "_rainflow",
    "_relative_damage",
    "_summary",
    "_comparison",
)

def read_drive_cycle_metadata(path):
    """
    Read '# key=value' metadata from a recorded mission profile.
    Values stay as strings here because this information is primarily provenance.
    """
    metadata = {}

    try:
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            for line in handle:
                if not line.startswith("#"):
                    break

                payload = line[1:].strip()

                if "=" not in payload:
                    continue

                key, value = payload.split("=", 1)
                metadata[key.strip()] = value.strip()
    except OSError:
        pass

    return metadata


def _location_label(metadata, prefix):
    location = metadata.get(
        f"{prefix}_location",
        metadata.get(
            f"route_{prefix}_location",
            "",
        ),
    ).strip()

    if location and location.lower() != "unknown":
        return location

    node_id = metadata.get(
        f"{prefix}_node_id",
        metadata.get(
            f"route_{prefix}_node_id",
            "",
        ),
    ).strip()

    latitude = metadata.get(
        f"{prefix}_lat",
        "",
    ).strip()

    longitude = metadata.get(
        f"{prefix}_lon",
        "",
    ).strip()

    # Prefer a node identifier because it is stable and directly corresponds
    # to the road graph used by the simulation.
    if node_id:
        if latitude and longitude:
            return (
                f"Node {node_id} "
                f"({latitude}, {longitude})"
            )

        return f"Node {node_id}"

    # If no node ID was saved, coordinates are still much more useful than
    # displaying a generic "Unknown".
    if latitude and longitude:
        return f"{latitude}, {longitude}"

    return "Unknown"


def mission_profile_location_fields(path):
    metadata = read_drive_cycle_metadata(path)

    return {
        "start_location": _location_label(
            metadata,
            "start",
        ),
        "end_location": _location_label(
            metadata,
            "end",
        ),
        "start_node_id": metadata.get(
            "start_node_id",
            metadata.get("route_start_node_id", ""),
        ),
        "end_node_id": metadata.get(
            "end_node_id",
            metadata.get("route_end_node_id", ""),
        ),
        "start_lat": metadata.get("start_lat", ""),
        "start_lon": metadata.get("start_lon", ""),
        "end_lat": metadata.get("end_lat", ""),
        "end_lon": metadata.get("end_lon", ""),
        "route_candidate_index": metadata.get("route_candidate_index", ""),
    }



def is_raw_drive_cycle_csv(path):
    path = Path(path)
    if path.suffix.lower() != ".csv":
        return False
    if any(path.stem.endswith(suffix) for suffix in DERIVED_SUFFIXES):
        return False

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                return [x.strip() for x in stripped.split(",")] == [
                    "time_s", "v_mps", "grade_deg"
                ]
    except OSError:
        return False

    return False


def discover_mission_profiles(cycles_dir):
    root = Path(cycles_dir)
    if not root.is_dir():
        return []

    rows = []
    for path in root.glob("*.csv"):
        if not is_raw_drive_cycle_csv(path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        location = mission_profile_location_fields(path)

        rows.append({
            "name": path.stem,
            "path": str(path.resolve()),
            "size_kb": stat.st_size / 1024.0,
            "modified": stat.st_mtime,
            **location,
        })

    rows.sort(key=lambda row: row["modified"], reverse=True)
    return rows


def _analyze_one_profile(args):
    cycle_path, config_path = args
    from drive_cycles.route_summary import analyze_route_summary

    summary = analyze_route_summary(cycle_path, config_path)
    data = asdict(summary)
    data["source_cycle"] = str(summary.source_cycle)
    data["profile_name"] = Path(cycle_path).stem
    data.update(
        mission_profile_location_fields(cycle_path)
    )
    data["analysis_ok"] = True
    data["analysis_error"] = None
    return data


def add_damage_vs_best(results):
    successful = [
        row for row in results
        if row.get("analysis_ok", True)
        and "total_relative_damage" in row
    ]
    if not successful:
        return results

    damages = [
        max(0.0, float(row["total_relative_damage"]))
        for row in successful
    ]
    minimum = min(damages)

    if minimum <= 0.0:
        for row, damage in zip(successful, damages):
            row["damage_vs_best"] = 1.0 if damage <= 0.0 else float("inf")
    else:
        for row, damage in zip(successful, damages):
            row["damage_vs_best"] = damage / minimum

    return results


def analyze_profiles_parallel(cycle_paths, vehicle_config_path, max_workers=None):
    paths = [str(Path(path).resolve()) for path in cycle_paths]
    if not paths:
        return []

    if max_workers is None:
        cpu_count = os.cpu_count() or 1
        max_workers = min(len(paths), max(1, cpu_count - 1))

    context = multiprocessing.get_context("spawn")
    results = []

    with ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=context,
    ) as executor:
        future_map = {
            executor.submit(
                _analyze_one_profile,
                (path, str(Path(vehicle_config_path).resolve())),
            ): path
            for path in paths
        }

        for future in as_completed(future_map):
            path = future_map[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    "profile_name": Path(path).stem,
                    "source_cycle": path,
                    "analysis_ok": False,
                    "analysis_error": f"{type(exc).__name__}: {exc}",
                }
            results.append(row)

    order = {str(Path(p).resolve()): i for i, p in enumerate(paths)}
    results.sort(
        key=lambda row: order.get(
            str(Path(row["source_cycle"]).resolve()),
            10**9,
        )
    )
    add_damage_vs_best(results)
    return results


def export_research_bundle(results, export_root, title="SiC mission-profile comparison"):
    successful = [row for row in results if row.get("analysis_ok", True)]
    if not successful:
        raise ValueError("No successful mission-profile analyses are available to export.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(export_root) / f"mission_profile_research_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)

    with (output_dir / "mission_profile_results.json").open(
        "w", encoding="utf-8"
    ) as handle:
        json.dump(
            {
                "title": title,
                "created": datetime.now().isoformat(timespec="seconds"),
                "profiles": results,
            },
            handle,
            indent=2,
        )

    fields = [
        "profile_name", "source_cycle",
        "start_location", "end_location",
        "start_node_id", "end_node_id",
        "start_lat", "start_lon", "end_lat", "end_lon",
        "route_candidate_index",
        "vehicle_name",
        "duration_s", "distance_km",
        "traction_energy_kwh", "recovered_energy_kwh", "net_dc_energy_kwh",
        "friction_brake_energy_kwh",
        "peak_wheel_power_kw", "peak_dc_propulsion_power_kw",
        "peak_dc_regen_power_kw", "peak_phase_current_a",
        "peak_aggregate_semiconductor_loss_w",
        "peak_representative_device_loss_w",
        "peak_case_temperature_c", "peak_junction_temperature_c",
        "minimum_junction_temperature_c",
        "rainflow_cycle_count", "equivalent_full_cycles",
        "maximum_delta_tj_c", "total_relative_damage", "damage_vs_best",
        "reliability_calibrated", "nonconverged_thermal_samples",
        "overtemperature_samples",
    ]

    with (output_dir / "mission_profile_comparison.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in successful:
            writer.writerow(row)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = []
    for index, row in enumerate(successful, start=1):
        name = str(row["profile_name"])
        labels.append(name if len(name) <= 28 else f"P{index}: {name[-20:]}")

    def save_bar(metric, ylabel, filename, scientific=False):
        values = [float(row[metric]) for row in successful]
        fig, ax = plt.subplots(figsize=(max(7.0, 1.4 * len(values)), 4.8))
        positions = list(range(len(values)))
        ax.bar(positions, values)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_xticks(positions)
        ax.set_xticklabels(labels, rotation=30, ha="right")
        ax.grid(axis="y", alpha=0.25)
        if scientific:
            ax.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
        fig.tight_layout()
        fig.savefig(output_dir / filename, dpi=220, bbox_inches="tight")
        plt.close(fig)

    save_bar("duration_s", "Duration (s)", "01_duration.png")
    save_bar("net_dc_energy_kwh", "Net DC energy (kWh)", "02_net_dc_energy.png")
    save_bar(
        "peak_junction_temperature_c",
        "Peak junction temperature (deg C)",
        "03_peak_junction_temperature.png",
    )
    save_bar(
        "total_relative_damage",
        "Relative SiC damage",
        "04_relative_damage.png",
        scientific=True,
    )
    save_bar(
        "maximum_delta_tj_c",
        "Maximum Delta Tj (deg C)",
        "05_max_delta_tj.png",
    )

    metrics = [
        ("duration_s", "Time"),
        ("net_dc_energy_kwh", "Energy"),
        ("total_relative_damage", "Damage"),
    ]

    normalized = {}
    for key, label in metrics:
        values = [float(row[key]) for row in successful]
        minimum = min(values)
        maximum = max(values)
        span = maximum - minimum
        normalized[label] = [
            0.0 if abs(span) <= 1e-15 else (value - minimum) / span
            for value in values
        ]

    fig, ax = plt.subplots(figsize=(max(7.5, 1.5 * len(successful)), 5.0))
    group_width = 0.78
    bar_width = group_width / len(metrics)
    centers = list(range(len(successful)))

    for metric_index, (_, label) in enumerate(metrics):
        offsets = [
            center - group_width / 2 + bar_width / 2 + metric_index * bar_width
            for center in centers
        ]
        ax.bar(offsets, normalized[label], width=bar_width, label=label)

    ax.set_title(f"{title} - normalized trade-off")
    ax.set_ylabel("Normalized metric (lower is better)")
    ax.set_xticks(centers)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylim(0.0, 1.08)
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(
        output_dir / "06_normalized_tradeoff.png",
        dpi=220,
        bbox_inches="tight",
    )
    plt.close(fig)

    with (output_dir / "README_results.txt").open(
        "w", encoding="utf-8"
    ) as handle:
        handle.write(title + "\n")
        handle.write("=" * len(title) + "\n\n")
        handle.write(f"Profiles analyzed: {len(successful)}\n\n")
        if any(not bool(row.get("reliability_calibrated", False)) for row in successful):
            handle.write(
                "NOTE: relative SiC damage is uncalibrated and intended for "
                "comparison, not absolute lifetime prediction.\n\n"
            )

        for row in successful:
            handle.write(f"{row['profile_name']}\n")
            handle.write(f"  Start: {row.get('start_location', 'Unknown')}\n")
            handle.write(f"  Destination: {row.get('end_location', 'Unknown')}\n")
            handle.write(f"  Duration: {float(row['duration_s']):.2f} s\n")
            handle.write(f"  Distance: {float(row['distance_km']):.4f} km\n")
            handle.write(f"  Net DC energy: {float(row['net_dc_energy_kwh']):.6f} kWh\n")
            handle.write(
                f"  Peak Tj: {float(row['peak_junction_temperature_c']):.2f} C\n"
            )
            handle.write(f"  Max Delta Tj: {float(row['maximum_delta_tj_c']):.3f} C\n")
            handle.write(
                f"  Relative damage: {float(row['total_relative_damage']):.6e}\n\n"
            )

    return output_dir
