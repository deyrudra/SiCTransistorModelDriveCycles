from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))

from drive_cycles.mission_profile_analysis import (
    discover_mission_profiles,
    analyze_profiles_parallel,
    export_research_bundle,
)


class MissionProfileLab:
    def __init__(self, root, *, cycles_dir, vehicle_config, export_dir):
        self.root = root
        self.cycles_dir = Path(cycles_dir)
        self.vehicle_config = Path(vehicle_config)
        self.export_dir = Path(export_dir)

        self.executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mission-profile-lab",
        )
        self.future = None
        self.future_kind = None
        self.results = []
        self.profile_rows = []

        self.status_var = tk.StringVar(
            value="Select two or more mission profiles, then Analyze selected."
        )

        root.title("Mission Profile Lab - SiC Drive-Cycle Comparison")
        root.geometry("1180x720")
        root.minsize(940, 580)

        self._build_ui()
        self.refresh_profiles()
        self._poll_future()

    def _build_ui(self):
        toolbar = ttk.Frame(self.root, padding=(10, 10, 10, 6))
        toolbar.pack(fill="x")

        ttk.Label(
            toolbar,
            text="MISSION PROFILE LAB",
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")

        ttk.Button(
            toolbar,
            text="Refresh",
            command=self.refresh_profiles,
        ).pack(side="right", padx=(6, 0))

        ttk.Button(
            toolbar,
            text="Open cycles folder",
            command=lambda: self.open_path(self.cycles_dir),
        ).pack(side="right", padx=(6, 0))

        panes = ttk.Panedwindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        left = ttk.Frame(panes, padding=8)
        right = ttk.Frame(panes, padding=8)
        panes.add(left, weight=2)
        panes.add(right, weight=5)

        ttk.Label(
            left,
            text="Recorded mission profiles",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            left,
            text="Ctrl/Shift-click to select multiple profiles.",
        ).pack(anchor="w", pady=(0, 6))

        list_frame = ttk.Frame(left)
        list_frame.pack(fill="both", expand=True)

        self.profile_list = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            font=("Consolas", 9),
            width=54,
        )

        scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.profile_list.yview,
        )

        self.profile_list.configure(
            yscrollcommand=scrollbar.set
        )

        self.profile_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        controls = ttk.Frame(left)
        controls.pack(fill="x", pady=(8, 0))

        self.analyze_button = ttk.Button(
            controls,
            text="Analyze selected",
            command=self.analyze_selected,
        )
        self.analyze_button.pack(side="left", fill="x", expand=True)

        ttk.Button(
            controls,
            text="Select all",
            command=lambda: self.profile_list.selection_set(0, tk.END),
        ).pack(side="left", padx=(6, 0))

        ttk.Label(
            right,
            text="Comparison results",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        columns = (
            "profile",
            "start",
            "end",
            "time",
            "distance",
            "energy",
            "peak_tj",
            "delta_tj",
            "damage",
            "damage_vs_best",
            "cycles",
        )

        tree_frame = ttk.Frame(right)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            height=16,
        )

        headings = {
            "profile": "Mission profile",
            "start": "Start",
            "end": "Destination",
            "time": "Time (s)",
            "distance": "Distance (km)",
            "energy": "Net energy (kWh)",
            "peak_tj": "Peak Tj (C)",
            "delta_tj": "Max ΔTj (C)",
            "damage": "Relative damage index",
            "damage_vs_best": "Damage vs best",
            "cycles": "Eq. cycles",
        }

        widths = {
            "profile": 220,
            "start": 210,
            "end": 210,
            "time": 80,
            "distance": 90,
            "energy": 110,
            "peak_tj": 90,
            "delta_tj": 95,
            "damage": 135,
            "damage_vs_best": 105,
            "cycles": 80,
        }

        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                anchor="w" if column == "profile" else "e",
            )

        yscroll = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        xscroll = ttk.Scrollbar(
            tree_frame,
            orient="horizontal",
            command=self.tree.xview,
        )

        self.tree.configure(
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set,
        )

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(8, 0))

        self.export_button = ttk.Button(
            buttons,
            text="Export research bundle",
            command=self.export_results,
            state="disabled",
        )
        self.export_button.pack(side="left")

        ttk.Button(
            buttons,
            text="Choose export folder",
            command=self.choose_export_folder,
        ).pack(side="left", padx=(6, 0))

        self.export_label = ttk.Label(
            buttons,
            text=str(self.export_dir),
        )
        self.export_label.pack(side="left", padx=(10, 0))

        status = ttk.Frame(self.root, padding=(10, 4, 10, 10))
        status.pack(fill="x")

        self.progress = ttk.Progressbar(
            status,
            mode="indeterminate",
            length=150,
        )
        self.progress.pack(side="left", padx=(0, 10))

        ttk.Label(
            status,
            textvariable=self.status_var,
        ).pack(side="left", fill="x", expand=True)

    def refresh_profiles(self):
        self.profile_rows = discover_mission_profiles(self.cycles_dir)
        self.profile_list.delete(0, tk.END)

        for row in self.profile_rows:
            modified = datetime.fromtimestamp(
                row["modified"]
            ).strftime("%Y-%m-%d %H:%M")

            start = str(row.get("start_location", "Unknown"))
            end = str(row.get("end_location", "Unknown"))

            if len(start) > 30:
                start = start[:27] + "..."
            if len(end) > 30:
                end = end[:27] + "..."

            self.profile_list.insert(
                tk.END,
                (
                    f"{row['name']}\n"
                    f"  {start}  ->  {end}\n"
                    f"  [{modified}, {row['size_kb']:.1f} KB]"
                ),
            )

        self.status_var.set(
            f"Found {len(self.profile_rows)} mission profiles in {self.cycles_dir}"
        )

    def analyze_selected(self):
        selection = list(self.profile_list.curselection())

        if len(selection) < 2:
            messagebox.showinfo(
                "Mission Profile Lab",
                "Select at least two mission profiles to compare.",
            )
            return

        paths = [
            self.profile_rows[index]["path"]
            for index in selection
        ]

        self.results = []
        self._clear_tree()
        self._set_busy(
            True,
            f"Analyzing {len(paths)} profiles in parallel...",
        )

        self.future_kind = "analysis"
        self.future = self.executor.submit(
            analyze_profiles_parallel,
            paths,
            self.vehicle_config,
        )

    def export_results(self):
        if not self.results:
            return

        self._set_busy(
            True,
            "Exporting CSV, JSON and publication plots...",
        )

        self.future_kind = "export"
        self.future = self.executor.submit(
            export_research_bundle,
            self.results,
            self.export_dir,
            title="SiC mission-profile comparison",
        )

    def _poll_future(self):
        if self.future is not None and self.future.done():
            future = self.future
            kind = self.future_kind
            self.future = None
            self.future_kind = None

            try:
                value = future.result()
            except Exception as exc:
                self._set_busy(False, f"{kind or 'Task'} failed: {exc}")
                messagebox.showerror(
                    "Mission Profile Lab",
                    str(exc),
                )
            else:
                if kind == "analysis":
                    self.results = value
                    self._populate_results()

                    ok_count = sum(
                        1
                        for row in self.results
                        if row.get("analysis_ok")
                    )

                    self._set_busy(
                        False,
                        f"Analysis complete: {ok_count}/{len(self.results)} successful.",
                    )

                    if ok_count:
                        self.export_button.configure(state="normal")

                elif kind == "export":
                    output_dir = Path(value)
                    self._set_busy(
                        False,
                        f"Research bundle exported to {output_dir}",
                    )

                    if messagebox.askyesno(
                        "Export complete",
                        f"Exported to:\n{output_dir}\n\nOpen the folder?",
                    ):
                        self.open_path(output_dir)

        self.root.after(150, self._poll_future)

    def _clear_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _populate_results(self):
        self._clear_tree()

        for row in self.results:
            if not row.get("analysis_ok"):
                self.tree.insert(
                    "",
                    tk.END,
                    values=(
                        row.get("profile_name", "unknown"),
                        row.get("start_location", "Unknown"),
                        row.get("end_location", "Unknown"),
                        "ERROR",
                        "",
                        "",
                        "",
                        "",
                        row.get("analysis_error", ""),
                        "",
                        "",
                    ),
                )
                continue

            self.tree.insert(
                "",
                tk.END,
                values=(
                    row["profile_name"],
                    row.get("start_location", "Unknown"),
                    row.get("end_location", "Unknown"),
                    f"{float(row['duration_s']):.2f}",
                    f"{float(row['distance_km']):.4f}",
                    f"{float(row['net_dc_energy_kwh']):.6f}",
                    f"{float(row['peak_junction_temperature_c']):.2f}",
                    f"{float(row['maximum_delta_tj_c']):.3f}",
                    f"{float(row['total_relative_damage']):.3e}",
                    (
                        "inf x"
                        if float(row.get("damage_vs_best", 1.0)) == float("inf")
                        else f"{float(row.get('damage_vs_best', 1.0)):.2f} x"
                    ),
                    f"{float(row['equivalent_full_cycles']):.2f}",
                ),
            )

    def choose_export_folder(self):
        selected = filedialog.askdirectory(
            initialdir=str(self.export_dir)
        )

        if selected:
            self.export_dir = Path(selected)
            self.export_label.configure(text=str(self.export_dir))

    def _set_busy(self, busy, status):
        self.status_var.set(status)

        self.analyze_button.configure(
            state="disabled" if busy else "normal"
        )

        self.export_button.configure(
            state="disabled" if busy or not self.results else "normal"
        )

        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()

    @staticmethod
    def open_path(path):
        path = str(Path(path).resolve())

        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def close(self):
        self.executor.shutdown(
            wait=False,
            cancel_futures=True,
        )
        self.root.destroy()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles-dir", type=Path, required=True)
    parser.add_argument("--vehicle-config", type=Path, required=True)
    parser.add_argument("--export-dir", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()

    root = tk.Tk()

    app = MissionProfileLab(
        root,
        cycles_dir=args.cycles_dir,
        vehicle_config=args.vehicle_config,
        export_dir=args.export_dir,
    )

    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()
