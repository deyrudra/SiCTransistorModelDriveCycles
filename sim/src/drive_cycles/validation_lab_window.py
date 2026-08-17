from __future__ import annotations

import argparse
import csv
import math
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

# Allow direct execution from sim/src/drive_cycles.
THIS_DIR = Path(__file__).resolve().parent
SRC_DIR = THIS_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from drive_cycles.vehicle_route_validation import (
    validate_vehicle_route,
    save_vehicle_route_validation,
)


RAW_HEADER = ["time_s", "v_mps", "grade_deg"]


def is_raw_drive_cycle(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() != ".csv":
        return False

    name = path.stem.lower()
    excluded = (
        "_vehicle_validation",
        "_longitudinal",
        "_inverter",
        "_thermal",
        "_rainflow",
        "_reliability",
        "_summary",
    )
    if any(name.endswith(suffix) for suffix in excluded):
        return False

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            lines = (
                line for line in handle
                if not line.lstrip().startswith("#")
            )
            reader = csv.reader(lines)
            header = next(reader, [])
        return header[:3] == RAW_HEADER
    except Exception:
        return False


class ValidationLab(tk.Tk):
    def __init__(
        self,
        cycles_dir: Path,
        vehicle_config: Path,
        export_dir: Path,
    ) -> None:
        super().__init__()

        self.cycles_dir = cycles_dir
        self.vehicle_config = vehicle_config
        self.export_dir = export_dir

        self.title("SiC Drive-Cycle Validation Lab")
        self.geometry("1500x900")
        self.minsize(1100, 680)

        self.profile_paths: list[Path] = []
        self.result_by_item: dict[str, object] = {}

        self._build_ui()
        self.refresh_profiles()

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(
            top,
            text="Validation Lab",
            font=("TkDefaultFont", 15, "bold"),
        ).pack(side="left")

        ttk.Label(
            top,
            text=f"Vehicle config: {self.vehicle_config.name}",
        ).pack(side="right")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.vehicle_tab = ttk.Frame(notebook)
        self.inverter_tab = ttk.Frame(notebook)
        self.thermal_tab = ttk.Frame(notebook)
        self.reliability_tab = ttk.Frame(notebook)

        notebook.add(self.vehicle_tab, text="1. Vehicle / Route")
        notebook.add(self.inverter_tab, text="2. Inverter")
        notebook.add(self.thermal_tab, text="3. Thermal")
        notebook.add(self.reliability_tab, text="4. Reliability")

        self._build_vehicle_tab()
        self._build_placeholder(
            self.inverter_tab,
            "Inverter validation",
            "Next stage: compare conduction and switching losses against "
            "manufacturer datasheet operating points.",
        )
        self._build_placeholder(
            self.thermal_tab,
            "Thermal validation",
            "Next stage: compare the Foster RC response against the selected "
            "SiC device transient Zth(j-c) curve.",
        )
        self._build_placeholder(
            self.reliability_tab,
            "Reliability validation",
            "Later stage: compare rainflow / damage-model behaviour against "
            "published power-cycling lifetime data.",
        )

    @staticmethod
    def _build_placeholder(
        parent: ttk.Frame,
        title: str,
        text: str,
    ) -> None:
        frame = ttk.Frame(parent, padding=35)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=title,
            font=("TkDefaultFont", 14, "bold"),
        ).pack(anchor="w", pady=(10, 8))

        ttk.Label(
            frame,
            text=text,
            wraplength=800,
        ).pack(anchor="w")

        ttk.Label(
            frame,
            text="Not implemented yet.",
            foreground="#777777",
        ).pack(anchor="w", pady=(18, 0))

    def _build_vehicle_tab(self) -> None:
        root = ttk.Frame(self.vehicle_tab, padding=10)
        root.pack(fill="both", expand=True)

        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(0, 8))

        ttk.Button(
            controls,
            text="Refresh profiles",
            command=self.refresh_profiles,
        ).pack(side="left")

        ttk.Button(
            controls,
            text="Analyze selected",
            command=self.analyze_selected,
        ).pack(side="left", padx=(8, 0))

        ttk.Button(
            controls,
            text="Analyze all",
            command=self.analyze_all,
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            controls,
            text="A Stuttgart route is a sanity comparison, not a WLTP certification test.",
        ).pack(side="right")

        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, padding=(0, 0, 8, 0))
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=4)

        ttk.Label(
            left,
            text="Drive-cycle profiles",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w")

        self.profile_list = tk.Listbox(
            left,
            selectmode=tk.EXTENDED,
            exportselection=False,
            width=38,
        )
        self.profile_list.pack(fill="both", expand=True, pady=(5, 0))

        self.profile_list.bind(
            "<Double-Button-1>",
            lambda _event: self.analyze_selected(),
        )

        summary_frame = ttk.LabelFrame(
            right,
            text="Vehicle / route validation results",
            padding=6,
        )
        summary_frame.pack(fill="both", expand=True)

        columns = (
            "profile",
            "distance",
            "duration",
            "avg_speed",
            "peak_speed",
            "stopped",
            "net_energy",
            "whkm",
            "benchmark",
            "error",
            "assessment",
        )

        self.results = ttk.Treeview(
            summary_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )

        headings = {
            "profile": "Profile",
            "distance": "Distance km",
            "duration": "Duration s",
            "avg_speed": "Avg km/h",
            "peak_speed": "Peak km/h",
            "stopped": "Stopped %",
            "net_energy": "Net DC kWh",
            "whkm": "Sim Wh/km",
            "benchmark": "Benchmark Wh/km",
            "error": "Error %",
            "assessment": "Assessment",
        }

        widths = {
            "profile": 220,
            "distance": 88,
            "duration": 85,
            "avg_speed": 85,
            "peak_speed": 85,
            "stopped": 80,
            "net_energy": 92,
            "whkm": 90,
            "benchmark": 112,
            "error": 75,
            "assessment": 135,
        }

        for key in columns:
            self.results.heading(key, text=headings[key])
            self.results.column(
                key,
                width=widths[key],
                minwidth=65,
                anchor="center" if key != "profile" else "w",
            )

        yscroll = ttk.Scrollbar(
            summary_frame,
            orient="vertical",
            command=self.results.yview,
        )
        xscroll = ttk.Scrollbar(
            summary_frame,
            orient="horizontal",
            command=self.results.xview,
        )
        self.results.configure(
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set,
        )

        self.results.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        summary_frame.rowconfigure(0, weight=1)
        summary_frame.columnconfigure(0, weight=1)

        lower = ttk.Panedwindow(
            right,
            orient="horizontal",
        )
        lower.pack(fill="x", pady=(8, 0))

        breakdown_frame = ttk.LabelFrame(
            lower,
            text="Energy breakdown",
            padding=6,
        )
        detail_frame = ttk.LabelFrame(
            lower,
            text="Selected result details",
            padding=6,
        )

        lower.add(breakdown_frame, weight=2)
        lower.add(detail_frame, weight=3)

        self.breakdown = ttk.Treeview(
            breakdown_frame,
            columns=("term", "kwh", "whkm"),
            show="headings",
            height=11,
        )
        self.breakdown.heading("term", text="Energy term")
        self.breakdown.heading("kwh", text="kWh")
        self.breakdown.heading("whkm", text="Wh/km")
        self.breakdown.column("term", width=230, anchor="w")
        self.breakdown.column("kwh", width=90, anchor="e")
        self.breakdown.column("whkm", width=90, anchor="e")
        self.breakdown.pack(fill="both", expand=True)

        ttk.Label(
            breakdown_frame,
            text=(
                "Inertial and grade terms are signed. "
                "Recovered regen is shown as a negative battery contribution."
            ),
            foreground="#666666",
            wraplength=430,
        ).pack(anchor="w", pady=(5, 0))

        self.details = tk.Text(
            detail_frame,
            height=14,
            wrap="word",
            state="disabled",
        )
        self.details.pack(fill="both", expand=True)

        self.results.bind(
            "<<TreeviewSelect>>",
            self._show_selected_result,
        )

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(
            root,
            textvariable=self.status_var,
            anchor="w",
        ).pack(fill="x", pady=(6, 0))

    def refresh_profiles(self) -> None:
        self.cycles_dir.mkdir(parents=True, exist_ok=True)

        self.profile_paths = sorted(
            (
                path
                for path in self.cycles_dir.glob("*.csv")
                if is_raw_drive_cycle(path)
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        self.profile_list.delete(0, tk.END)
        for path in self.profile_paths:
            self.profile_list.insert(tk.END, path.name)

        self.status_var.set(
            f"Found {len(self.profile_paths)} raw drive-cycle profile(s)"
        )

    def _selected_paths(self) -> list[Path]:
        return [
            self.profile_paths[index]
            for index in self.profile_list.curselection()
        ]

    def analyze_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo(
                "Validation Lab",
                "Select one or more drive-cycle profiles first.",
            )
            return
        self._analyze_paths(paths)

    def analyze_all(self) -> None:
        if not self.profile_paths:
            messagebox.showinfo(
                "Validation Lab",
                "No raw drive-cycle profiles were found.",
            )
            return
        self._analyze_paths(self.profile_paths)

    def _analyze_paths(self, paths: list[Path]) -> None:
        self.status_var.set(
            f"Analyzing {len(paths)} profile(s)..."
        )
        self.update_idletasks()

        failures = []

        for index, path in enumerate(paths, start=1):
            self.status_var.set(
                f"Analyzing {index}/{len(paths)}: {path.name}"
            )
            self.update_idletasks()

            try:
                result = validate_vehicle_route(
                    path,
                    self.vehicle_config,
                )

                save_vehicle_route_validation(result)
                self._insert_or_replace_result(result)

            except Exception as exc:
                failures.append(
                    f"{path.name}: {exc}"
                )

        if failures:
            self.status_var.set(
                f"Finished with {len(failures)} failure(s)"
            )
            messagebox.showwarning(
                "Validation completed with errors",
                "\n\n".join(failures[:8]),
            )
        else:
            self.status_var.set(
                f"Validation complete: {len(paths)} profile(s)"
            )

    def _insert_or_replace_result(self, result) -> None:
        existing = None
        source = str(Path(result.source_cycle).resolve())

        for item_id, saved in self.result_by_item.items():
            if str(Path(saved.source_cycle).resolve()) == source:
                existing = item_id
                break

        error_text = f"{result.energy_error_percent:+.2f}"

        values = (
            result.profile_name,
            f"{result.distance_km:.3f}",
            f"{result.duration_s:.1f}",
            f"{result.average_speed_kmh:.2f}",
            f"{result.peak_speed_kmh:.2f}",
            f"{result.stopped_time_percent:.2f}",
            f"{result.net_dc_energy_kwh:.4f}",
            f"{result.simulated_wh_per_km:.2f}",
            f"{result.benchmark_wh_per_km:.2f}",
            error_text,
            result.energy_assessment,
        )

        if existing is not None:
            self.results.item(existing, values=values)
            self.result_by_item[existing] = result
            item_id = existing
        else:
            item_id = self.results.insert(
                "",
                "end",
                values=values,
            )
            self.result_by_item[item_id] = result

        self.results.selection_set(item_id)
        self.results.see(item_id)
        self._show_selected_result()

    def _populate_breakdown(self, result) -> None:
        for item in self.breakdown.get_children():
            self.breakdown.delete(item)

        distance_km = result.distance_km

        rows = [
            ("Aerodynamic energy", result.aerodynamic_energy_kwh),
            (
                "Rolling-resistance energy",
                result.rolling_resistance_energy_kwh,
            ),
            (
                "Acceleration / inertial energy",
                result.inertial_energy_kwh,
            ),
            ("Grade energy", result.grade_energy_kwh),
            (
                "Drivetrain losses",
                result.drivetrain_loss_energy_kwh,
            ),
            (
                "Recovered regen energy",
                -result.recovered_regen_energy_breakdown_kwh,
            ),
            (
                "Auxiliary energy",
                result.auxiliary_energy_kwh,
            ),
            (
                "Net battery energy",
                result.net_battery_energy_breakdown_kwh,
            ),
        ]

        for label, energy_kwh in rows:
            wh_per_km = (
                energy_kwh * 1000.0 / distance_km
                if distance_km > 0.0
                else 0.0
            )

            self.breakdown.insert(
                "",
                "end",
                values=(
                    label,
                    f"{energy_kwh:+.6f}",
                    f"{wh_per_km:+.2f}",
                ),
            )

    def _show_selected_result(self, _event=None) -> None:
        selected = self.results.selection()
        if not selected:
            return

        result = self.result_by_item.get(selected[0])
        if result is None:
            return

        self._populate_breakdown(result)

        ledger_difference_kwh = (
            result.net_battery_energy_breakdown_kwh
            - result.net_dc_energy_kwh
        )

        lines = [
            f"Profile: {result.profile_name}",
            f"Vehicle: {result.vehicle_name}",
            f"Benchmark: {result.benchmark_name}",
            f"Certification reference: {result.benchmark_cycle}",
            "",
            "ENERGY",
            f"  Traction energy:       {result.traction_energy_kwh:.6f} kWh",
            f"  Recovered energy:      {result.recovered_energy_kwh:.6f} kWh",
            f"  Net DC energy:         {result.net_dc_energy_kwh:.6f} kWh",
            f"  Regen / traction:      {result.recovered_fraction_percent:.2f} %",
            f"  Simulated consumption: {result.simulated_wh_per_km:.2f} Wh/km",
            f"  Benchmark consumption: {result.benchmark_wh_per_km:.2f} Wh/km",
            f"  Difference:            {result.energy_error_wh_per_km:+.2f} Wh/km",
            f"  Percentage error:      {result.energy_error_percent:+.2f} %",
            f"  Assessment:            {result.energy_assessment}",
            "",
            "INDEPENDENT ENERGY LEDGER",
            f"  Aerodynamic:           {result.aerodynamic_energy_kwh:+.6f} kWh",
            f"  Rolling resistance:    {result.rolling_resistance_energy_kwh:+.6f} kWh",
            f"  Inertial:              {result.inertial_energy_kwh:+.6f} kWh",
            f"  Grade:                 {result.grade_energy_kwh:+.6f} kWh",
            f"  Drivetrain losses:     {result.drivetrain_loss_energy_kwh:+.6f} kWh",
            f"  Recovered regen:       {-result.recovered_regen_energy_breakdown_kwh:+.6f} kWh",
            f"  Auxiliary:             {result.auxiliary_energy_kwh:+.6f} kWh",
            f"  Net battery estimate:  {result.net_battery_energy_breakdown_kwh:+.6f} kWh",
            f"  Ledger consumption:    {result.breakdown_wh_per_km:.2f} Wh/km",
            f"  Ledger - model net DC: {ledger_difference_kwh:+.6f} kWh",
            "",
            "SPEED / ROUTE BEHAVIOUR",
            f"  Distance:              {result.distance_km:.3f} km",
            f"  Duration:              {result.duration_s:.1f} s",
            f"  Average speed:         {result.average_speed_kmh:.2f} km/h",
            f"  Peak speed:            {result.peak_speed_kmh:.2f} km/h",
            f"  Stopped time:          {result.stopped_time_percent:.2f} %",
            f"  Mean acceleration:     {result.mean_positive_accel_mps2:.3f} m/s2",
            f"  P95 acceleration:      {result.p95_positive_accel_mps2:.3f} m/s2",
            f"  Mean braking:          {result.mean_braking_mps2:.3f} m/s2",
            f"  P95 braking:           {result.p95_braking_magnitude_mps2:.3f} m/s2",
            "",
            "WLTC CLASS 3 CONTEXT",
            f"  Average-speed delta:   {result.wltc_average_speed_delta_kmh:+.2f} km/h",
            f"  Peak-speed delta:      {result.wltc_peak_speed_delta_kmh:+.2f} km/h",
            f"  Stopped-time delta:    {result.wltc_stopped_time_delta_percent:+.2f} %-pt",
            "",
            result.comparison_scope,
        ]

        self.details.configure(state="normal")
        self.details.delete("1.0", tk.END)
        self.details.insert("1.0", "\n".join(lines))
        self.details.configure(state="disabled")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GUI validation laboratory for the drive-cycle project."
    )
    parser.add_argument(
        "--cycles-dir",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--vehicle-config",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        required=True,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    app = ValidationLab(
        cycles_dir=args.cycles_dir,
        vehicle_config=args.vehicle_config,
        export_dir=args.export_dir,
    )
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
