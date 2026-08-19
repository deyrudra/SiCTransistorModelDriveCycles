from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox

SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))


from drive_cycles.experiment_lab_analysis import (
    analyze_group,
    discover_profiles,
    export_experiment_csv,
    group_mean,
    normalized_balanced_ranking,
)
from drive_cycles.experiment_route_generator import (
    generate_pair_experiment,
    generate_ranking_experiment,
    preset_for,
)


PAIR_EXPERIMENTS = (
    {
        "key": "flat_uphill",
        "title": "1. Flat vs Uphill",
        "group_a": "Flat / lower-ascent route(s)",
        "group_b": "Uphill / higher-ascent route(s)",
        "help": (
            "Use routes with similar distance and endpoints, but clearly different "
            "total ascent. Keep traffic settings and seeds the same."
        ),
    },
    {
        "key": "uphill_downhill",
        "title": "2. Uphill vs Downhill",
        "group_a": "Uphill direction",
        "group_b": "Downhill reverse direction",
        "help": (
            "Use the same road corridor in opposite directions to isolate climbing "
            "energy versus regenerative recovery."
        ),
    },
    {
        "key": "short_distance",
        "title": "3. Short Distance",
        "group_a": "Short-route candidate A / set A",
        "group_b": "Short-route candidate B / set B",
        "help": (
            "Compare alternative routes of roughly 2-4 km to see whether route "
            "choice materially affects energy and SiC stress on short trips."
        ),
    },
    {
        "key": "long_distance",
        "title": "4. Long Distance",
        "group_a": "Long-route candidate A / set A",
        "group_b": "Long-route candidate B / set B",
        "help": (
            "Compare longer alternatives (for example 15-25 km) to see how small "
            "differences accumulate into energy and reliability differences."
        ),
    },
    {
        "key": "fast_slow",
        "title": "5. Fast vs Slow Roads",
        "group_a": "Faster / arterial route(s)",
        "group_b": "Slower / local-road route(s)",
        "help": (
            "Prefer similar-distance routes. This tests whether smooth high-speed "
            "driving or lower-speed stop-start driving is gentler on the inverter."
        ),
    },
    {
        "key": "traffic",
        "title": "6. Traffic",
        "group_a": "No / light traffic",
        "group_b": "Heavy traffic",
        "help": (
            "Use the exact same route. For stochastic traffic, select several seeds "
            "in each group so the GUI compares group means rather than one run."
        ),
    },
    {
        "key": "stop_start",
        "title": "7. Stop-Start vs Free-Flow",
        "group_a": "Free-flow / fewer stops",
        "group_b": "Stop-start / many stops",
        "help": (
            "Use similar-distance routes or the same corridor under different "
            "conditions to isolate repeated acceleration/braking and thermal cycling."
        ),
    },
)


RESULT_COLUMNS = (
    "group",
    "profile",
    "distance",
    "ascent",
    "time",
    "avg_speed",
    "stopped",
    "energy",
    "whkm",
    "peak_tj",
    "delta_tj",
    "damage",
)


class PairExperimentTab:
    def __init__(
        self,
        parent,
        *,
        spec,
        app,
    ):
        self.app = app
        self.spec = spec
        self.rows = []
        self.generated_all_paths = []

        self.preset = preset_for(
            self.spec["key"]
        )

        self.start_address_var = tk.StringVar(
            value=self.preset["start"]
        )
        self.end_address_var = tk.StringVar(
            value=self.preset["end"]
        )
        self.candidate_count_var = tk.IntVar(
            value=int(
                self.preset.get(
                    "candidate_count",
                    5,
                )
            )
        )
        self.distance_tolerance_var = tk.DoubleVar(
            value=float(
                self.preset.get(
                    "distance_tolerance_percent",
                    15.0,
                )
            )
        )
        self.traffic_count_var = tk.IntVar(
            value=int(
                self.preset.get(
                    "traffic_count",
                    20,
                )
            )
        )
        self.random_seed_var = tk.IntVar(
            value=int(
                self.preset.get(
                    "seed",
                    7,
                )
            )
        )
        self.low_traffic_var = tk.IntVar(
            value=int(
                self.preset.get(
                    "low_traffic_count",
                    0,
                )
            )
        )
        self.high_traffic_var = tk.IntVar(
            value=int(
                self.preset.get(
                    "high_traffic_count",
                    100,
                )
            )
        )
        self.traffic_seeds_var = tk.StringVar(
            value=",".join(
                str(value)
                for value in self.preset.get(
                    "seeds",
                    (1, 2, 3, 4, 5),
                )
            )
        )

        root = ttk.Frame(
            parent,
            padding=10,
        )
        root.pack(
            fill="both",
            expand=True,
        )

        ttk.Label(
            root,
            text=spec["title"],
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            root,
            text=spec["help"],
            wraplength=1180,
        ).pack(
            anchor="w",
            pady=(3, 8),
        )

        self._build_generator_controls(
            root
        )

        selection = ttk.Panedwindow(
            root,
            orient="horizontal",
        )
        selection.pack(
            fill="both",
            expand=False,
        )

        self.list_a = self._build_group(
            selection,
            spec["group_a"],
        )
        self.list_b = self._build_group(
            selection,
            spec["group_b"],
        )

        buttons = ttk.Frame(root)
        buttons.pack(
            fill="x",
            pady=(8, 6),
        )

        self.analyze_button = ttk.Button(
            buttons,
            text="Analyze A vs B",
            command=self.analyze,
        )
        self.analyze_button.pack(
            side="left",
        )

        ttk.Button(
            buttons,
            text="Refresh profiles",
            command=self.app.refresh_profiles,
        ).pack(
            side="left",
            padx=(8, 0),
        )

        self.export_button = ttk.Button(
            buttons,
            text="Export experiment CSV",
            command=self.export,
            state="disabled",
        )
        self.export_button.pack(
            side="left",
            padx=(8, 0),
        )

        self.summary_var = tk.StringVar(
            value="Select one or more profiles in each group."
        )

        ttk.Label(
            buttons,
            textvariable=self.summary_var,
        ).pack(
            side="right",
        )

        results_frame = ttk.LabelFrame(
            root,
            text="Individual runs",
            padding=5,
        )
        results_frame.pack(
            fill="both",
            expand=True,
        )

        self.tree = ttk.Treeview(
            results_frame,
            columns=RESULT_COLUMNS,
            show="headings",
            height=10,
        )

        headings = {
            "group": "Group",
            "profile": "Profile",
            "distance": "km",
            "ascent": "Ascent m",
            "time": "Time s",
            "avg_speed": "Avg km/h",
            "stopped": "Stopped %",
            "energy": "Battery kWh",
            "whkm": "Wh/km",
            "peak_tj": "Peak Tj C",
            "delta_tj": "Max DeltaTj C",
            "damage": "Relative damage",
        }

        widths = {
            "group": 190,
            "profile": 245,
            "distance": 75,
            "ascent": 80,
            "time": 80,
            "avg_speed": 90,
            "stopped": 90,
            "energy": 100,
            "whkm": 90,
            "peak_tj": 90,
            "delta_tj": 110,
            "damage": 115,
        }

        for column in RESULT_COLUMNS:
            self.tree.heading(
                column,
                text=headings[column],
            )
            self.tree.column(
                column,
                width=widths[column],
                anchor=(
                    "w"
                    if column in ("group", "profile")
                    else "center"
                ),
            )

        yscroll = ttk.Scrollbar(
            results_frame,
            orient="vertical",
            command=self.tree.yview,
        )

        xscroll = ttk.Scrollbar(
            results_frame,
            orient="horizontal",
            command=self.tree.xview,
        )

        self.tree.configure(
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set,
        )

        self.tree.grid(
            row=0,
            column=0,
            sticky="nsew",
        )
        yscroll.grid(
            row=0,
            column=1,
            sticky="ns",
        )
        xscroll.grid(
            row=1,
            column=0,
            sticky="ew",
        )

        results_frame.rowconfigure(
            0,
            weight=1,
        )
        results_frame.columnconfigure(
            0,
            weight=1,
        )

        summary_frame = ttk.LabelFrame(
            root,
            text="Group-mean comparison",
            padding=5,
        )
        summary_frame.pack(
            fill="x",
            pady=(8, 0),
        )

        self.summary_text = tk.Text(
            summary_frame,
            height=7,
            state="disabled",
            font=("Consolas", 9),
        )
        self.summary_text.pack(
            fill="x",
        )

    def _build_generator_controls(
        self,
        parent,
    ):
        frame = ttk.LabelFrame(
            parent,
            text="Generate Stuttgart experiment",
            padding=7,
        )
        frame.pack(
            fill="x",
            pady=(0, 8),
        )

        ttk.Label(
            frame,
            text="A:",
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )

        ttk.Entry(
            frame,
            textvariable=self.start_address_var,
            width=48,
        ).grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(5, 10),
        )

        ttk.Label(
            frame,
            text="B:",
        ).grid(
            row=0,
            column=4,
            sticky="w",
        )

        ttk.Entry(
            frame,
            textvariable=self.end_address_var,
            width=48,
        ).grid(
            row=0,
            column=5,
            columnspan=3,
            sticky="ew",
            padx=(5, 10),
        )

        ttk.Button(
            frame,
            text="Reset Stuttgart preset",
            command=self.reset_preset,
        ).grid(
            row=0,
            column=8,
            padx=(4, 0),
        )

        ttk.Label(
            frame,
            text="Candidates:",
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(7, 0),
        )

        ttk.Spinbox(
            frame,
            from_=1,
            to=12,
            textvariable=self.candidate_count_var,
            width=6,
        ).grid(
            row=1,
            column=1,
            sticky="w",
            pady=(7, 0),
        )

        ttk.Label(
            frame,
            text="Distance tolerance %:",
        ).grid(
            row=1,
            column=2,
            sticky="e",
            padx=(8, 4),
            pady=(7, 0),
        )

        ttk.Spinbox(
            frame,
            from_=0.0,
            to=100.0,
            increment=1.0,
            textvariable=self.distance_tolerance_var,
            width=7,
        ).grid(
            row=1,
            column=3,
            sticky="w",
            pady=(7, 0),
        )

        if self.spec["key"] == "traffic":
            ttk.Label(
                frame,
                text="Low traffic:",
            ).grid(
                row=1,
                column=4,
                sticky="e",
                padx=(8, 4),
                pady=(7, 0),
            )

            ttk.Spinbox(
                frame,
                from_=0,
                to=300,
                textvariable=self.low_traffic_var,
                width=7,
            ).grid(
                row=1,
                column=5,
                sticky="w",
                pady=(7, 0),
            )

            ttk.Label(
                frame,
                text="Heavy traffic:",
            ).grid(
                row=1,
                column=6,
                sticky="e",
                padx=(8, 4),
                pady=(7, 0),
            )

            ttk.Spinbox(
                frame,
                from_=0,
                to=300,
                textvariable=self.high_traffic_var,
                width=7,
            ).grid(
                row=1,
                column=7,
                sticky="w",
                pady=(7, 0),
            )

            ttk.Label(
                frame,
                text="Seeds:",
            ).grid(
                row=2,
                column=0,
                sticky="w",
                pady=(5, 0),
            )

            ttk.Entry(
                frame,
                textvariable=self.traffic_seeds_var,
                width=22,
            ).grid(
                row=2,
                column=1,
                columnspan=2,
                sticky="w",
                pady=(5, 0),
            )
        else:
            ttk.Label(
                frame,
                text="Background traffic:",
            ).grid(
                row=1,
                column=4,
                sticky="e",
                padx=(8, 4),
                pady=(7, 0),
            )

            ttk.Spinbox(
                frame,
                from_=0,
                to=300,
                textvariable=self.traffic_count_var,
                width=7,
            ).grid(
                row=1,
                column=5,
                sticky="w",
                pady=(7, 0),
            )

            ttk.Label(
                frame,
                text="Seed:",
            ).grid(
                row=1,
                column=6,
                sticky="e",
                padx=(8, 4),
                pady=(7, 0),
            )

            ttk.Spinbox(
                frame,
                from_=0,
                to=999999,
                textvariable=self.random_seed_var,
                width=8,
            ).grid(
                row=1,
                column=7,
                sticky="w",
                pady=(7, 0),
            )

        self.generate_button = ttk.Button(
            frame,
            text="Generate + Run + Analyze",
            command=self.generate_and_analyze,
        )
        self.generate_button.grid(
            row=1,
            column=8,
            rowspan=2,
            sticky="nsew",
            padx=(10, 0),
            pady=(7, 0),
        )

        frame.columnconfigure(
            1,
            weight=1,
        )
        frame.columnconfigure(
            5,
            weight=1,
        )

    def reset_preset(
        self,
    ):
        self.preset = preset_for(
            self.spec["key"]
        )

        self.start_address_var.set(
            self.preset["start"]
        )
        self.end_address_var.set(
            self.preset["end"]
        )
        self.candidate_count_var.set(
            int(
                self.preset.get(
                    "candidate_count",
                    5,
                )
            )
        )
        self.distance_tolerance_var.set(
            float(
                self.preset.get(
                    "distance_tolerance_percent",
                    15.0,
                )
            )
        )
        self.traffic_count_var.set(
            int(
                self.preset.get(
                    "traffic_count",
                    20,
                )
            )
        )
        self.random_seed_var.set(
            int(
                self.preset.get(
                    "seed",
                    7,
                )
            )
        )
        self.low_traffic_var.set(
            int(
                self.preset.get(
                    "low_traffic_count",
                    0,
                )
            )
        )
        self.high_traffic_var.set(
            int(
                self.preset.get(
                    "high_traffic_count",
                    100,
                )
            )
        )
        self.traffic_seeds_var.set(
            ",".join(
                str(value)
                for value in self.preset.get(
                    "seeds",
                    (1, 2, 3, 4, 5),
                )
            )
        )

    def _parse_traffic_seeds(
        self,
    ):
        values = []

        for item in self.traffic_seeds_var.get().split(","):
            item = item.strip()

            if not item:
                continue

            values.append(
                int(
                    item
                )
            )

        if not values:
            raise ValueError(
                "Enter at least one traffic seed, for example 1,2,3,4,5."
            )

        return tuple(
            values
        )

    def generate_and_analyze(
        self,
    ):
        start = self.start_address_var.get().strip()
        end = self.end_address_var.get().strip()

        if not start or not end:
            messagebox.showinfo(
                self.spec["title"],
                "Enter both Stuttgart addresses.",
            )
            return

        try:
            seeds = self._parse_traffic_seeds()
        except Exception as exc:
            if self.spec["key"] == "traffic":
                messagebox.showerror(
                    "Traffic seeds",
                    str(exc),
                )
                return
            seeds = (1, 2, 3, 4, 5)

        params = {
            "experiment_key": self.spec["key"],
            "project_root": self.app.project_root,
            "vehicle_config_path": self.app.vehicle_config,
            "cycles_dir": self.app.cycles_dir,
            "start_address": start,
            "end_address": end,
            "candidate_count": int(
                self.candidate_count_var.get()
            ),
            "distance_tolerance_percent": float(
                self.distance_tolerance_var.get()
            ),
            "traffic_count": int(
                self.traffic_count_var.get()
            ),
            "random_seed": int(
                self.random_seed_var.get()
            ),
            "low_traffic_count": int(
                self.low_traffic_var.get()
            ),
            "high_traffic_count": int(
                self.high_traffic_var.get()
            ),
            "traffic_seeds": seeds,
        }

        self.app.run_background(
            kind=("pair", self),
            function=self._generate_worker,
            args=(params,),
            status=(
                f"{self.spec['title']}: generating routes, "
                "running fixed-step simulations and analyzing results..."
            ),
        )

    def _generate_worker(
        self,
        params,
    ):
        generated = generate_pair_experiment(
            **params
        )

        rows_a = analyze_group(
            generated.group_a_paths,
            self.app.vehicle_config,
        )
        rows_b = analyze_group(
            generated.group_b_paths,
            self.app.vehicle_config,
        )

        for row in rows_a:
            row["experiment_group"] = (
                self.spec["group_a"]
            )

        for row in rows_b:
            row["experiment_group"] = (
                self.spec["group_b"]
            )

        return {
            "rows_a": rows_a,
            "rows_b": rows_b,
            "generated_group_a_paths": list(
                generated.group_a_paths
            ),
            "generated_group_b_paths": list(
                generated.group_b_paths
            ),
            "generated_all_paths": list(
                generated.all_paths
            ),
            "generation_notes": list(
                generated.notes
            ),
        }

    def _select_paths(
        self,
        listbox,
        paths,
    ):
        wanted = {
            str(
                Path(path).resolve()
            )
            for path in paths
        }

        listbox.selection_clear(
            0,
            tk.END,
        )

        for index, row in enumerate(
            self.app.profile_rows
        ):
            if (
                str(
                    Path(
                        row["path"]
                    ).resolve()
                )
                in wanted
            ):
                listbox.selection_set(
                    index
                )

    def _build_group(
        self,
        parent,
        title,
    ):
        frame = ttk.LabelFrame(
            parent,
            text=title,
            padding=6,
        )
        parent.add(
            frame,
            weight=1,
        )

        ttk.Label(
            frame,
            text="Ctrl/Shift-click for repeated runs/seeds.",
        ).pack(
            anchor="w",
            pady=(0, 4),
        )

        listbox = tk.Listbox(
            frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            height=7,
            font=("Consolas", 8),
        )
        listbox.pack(
            fill="both",
            expand=True,
        )

        return listbox

    def refresh(
        self,
        profile_rows,
    ):
        selected_a = self._selected_paths(
            self.list_a
        )
        selected_b = self._selected_paths(
            self.list_b
        )

        for listbox in (
            self.list_a,
            self.list_b,
        ):
            listbox.delete(
                0,
                tk.END,
            )

            for row in profile_rows:
                listbox.insert(
                    tk.END,
                    row["name"],
                )

        self._restore_selection(
            self.list_a,
            selected_a,
        )
        self._restore_selection(
            self.list_b,
            selected_b,
        )

    def _restore_selection(
        self,
        listbox,
        paths,
    ):
        names = {
            Path(path).stem
            for path in paths
        }

        for index, row in enumerate(
            self.app.profile_rows
        ):
            if row["name"] in names:
                listbox.selection_set(
                    index
                )

    def _selected_paths(
        self,
        listbox,
    ):
        paths = []

        for index in listbox.curselection():
            if index < len(
                self.app.profile_rows
            ):
                paths.append(
                    self.app.profile_rows[
                        index
                    ]["path"]
                )

        return paths

    def analyze(self):
        paths_a = self._selected_paths(
            self.list_a
        )
        paths_b = self._selected_paths(
            self.list_b
        )

        if not paths_a or not paths_b:
            messagebox.showinfo(
                self.spec["title"],
                "Select at least one profile in both Group A and Group B.",
            )
            return

        self.app.run_background(
            kind=("pair", self),
            function=self._analyze_worker,
            args=(paths_a, paths_b),
            status=(
                f"{self.spec['title']}: running full electro-thermal "
                "and reliability analysis..."
            ),
        )

    def _analyze_worker(
        self,
        paths_a,
        paths_b,
    ):
        rows_a = analyze_group(
            paths_a,
            self.app.vehicle_config,
        )
        rows_b = analyze_group(
            paths_b,
            self.app.vehicle_config,
        )

        for row in rows_a:
            row["experiment_group"] = (
                self.spec["group_a"]
            )

        for row in rows_b:
            row["experiment_group"] = (
                self.spec["group_b"]
            )

        return {
            "rows_a": rows_a,
            "rows_b": rows_b,
        }

    def receive_result(
        self,
        payload,
    ):
        rows_a = payload["rows_a"]
        rows_b = payload["rows_b"]

        self.rows = (
            rows_a
            + rows_b
        )

        self._populate_tree()
        self._populate_summary(
            rows_a,
            rows_b,
        )

        if payload.get(
            "generated_all_paths"
        ):
            self.generated_all_paths = list(
                payload["generated_all_paths"]
            )

            self.app.refresh_profiles()

            self._select_paths(
                self.list_a,
                payload.get(
                    "generated_group_a_paths",
                    (),
                ),
            )
            self._select_paths(
                self.list_b,
                payload.get(
                    "generated_group_b_paths",
                    (),
                ),
            )

        self.export_button.configure(
            state="normal",
        )

        notes = payload.get(
            "generation_notes",
            (),
        )

        note_text = (
            " | " + " ".join(notes)
            if notes
            else ""
        )

        self.summary_var.set(
            (
                f"Complete: {len(rows_a)} A run(s), "
                f"{len(rows_b)} B run(s)"
                f"{note_text}"
            )
        )

    def receive_error(
        self,
        exc,
    ):
        self.summary_var.set(
            "Analysis failed."
        )

        messagebox.showerror(
            f"{self.spec['title']} failed",
            str(exc),
        )

    def _populate_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(
                item
            )

        for row in self.rows:
            self.tree.insert(
                "",
                tk.END,
                values=(
                    row["experiment_group"],
                    row["profile_name"],
                    f"{float(row['distance_km']):.3f}",
                    f"{float(row['ascent_m']):.1f}",
                    f"{float(row['duration_s']):.1f}",
                    f"{float(row['average_speed_kmh']):.2f}",
                    f"{float(row['stopped_time_percent']):.1f}",
                    f"{float(row['net_dc_energy_kwh']):.4f}",
                    f"{float(row['wh_per_km']):.1f}",
                    f"{float(row['peak_junction_temperature_c']):.2f}",
                    f"{float(row['maximum_delta_tj_c']):.2f}",
                    f"{float(row['total_relative_damage']):.3e}",
                ),
            )

    def _populate_summary(
        self,
        rows_a,
        rows_b,
    ):
        a = group_mean(
            rows_a
        )
        b = group_mean(
            rows_b
        )

        def pct_change(
            value_a,
            value_b,
        ):
            if abs(
                value_a
            ) <= 1e-15:
                return "n/a"
            return (
                f"{100.0 * (value_b - value_a) / value_a:+.1f}%"
            )

        lines = [
            (
                f"{'Metric':<22}"
                f"{'Group A mean':>16}"
                f"{'Group B mean':>16}"
                f"{'B vs A':>12}"
            ),
            "-" * 66,
            (
                f"{'Distance (km)':<22}"
                f"{a['distance_km']:>16.3f}"
                f"{b['distance_km']:>16.3f}"
                f"{pct_change(a['distance_km'], b['distance_km']):>12}"
            ),
            (
                f"{'Ascent (m)':<22}"
                f"{a['ascent_m']:>16.1f}"
                f"{b['ascent_m']:>16.1f}"
                f"{pct_change(a['ascent_m'], b['ascent_m']):>12}"
            ),
            (
                f"{'Time (s)':<22}"
                f"{a['duration_s']:>16.1f}"
                f"{b['duration_s']:>16.1f}"
                f"{pct_change(a['duration_s'], b['duration_s']):>12}"
            ),
            (
                f"{'Battery (kWh)':<22}"
                f"{a['net_dc_energy_kwh']:>16.4f}"
                f"{b['net_dc_energy_kwh']:>16.4f}"
                f"{pct_change(a['net_dc_energy_kwh'], b['net_dc_energy_kwh']):>12}"
            ),
            (
                f"{'Peak Tj (C)':<22}"
                f"{a['peak_junction_temperature_c']:>16.2f}"
                f"{b['peak_junction_temperature_c']:>16.2f}"
                f"{pct_change(a['peak_junction_temperature_c'], b['peak_junction_temperature_c']):>12}"
            ),
            (
                f"{'Max DeltaTj (C)':<22}"
                f"{a['maximum_delta_tj_c']:>16.2f}"
                f"{b['maximum_delta_tj_c']:>16.2f}"
                f"{pct_change(a['maximum_delta_tj_c'], b['maximum_delta_tj_c']):>12}"
            ),
            (
                f"{'Relative damage':<22}"
                f"{a['total_relative_damage']:>16.3e}"
                f"{b['total_relative_damage']:>16.3e}"
                f"{pct_change(a['total_relative_damage'], b['total_relative_damage']):>12}"
            ),
        ]

        self.summary_text.configure(
            state="normal"
        )
        self.summary_text.delete(
            "1.0",
            tk.END,
        )
        self.summary_text.insert(
            "1.0",
            "\n".join(lines),
        )
        self.summary_text.configure(
            state="disabled"
        )

    def export(self):
        if not self.rows:
            return

        try:
            path = export_experiment_csv(
                experiment_name=self.spec["title"],
                rows=self.rows,
                output_dir=(
                    self.app.export_dir
                    / "route_experiments"
                ),
            )
        except Exception as exc:
            messagebox.showerror(
                "Export failed",
                str(exc),
            )
            return

        self.summary_var.set(
            f"Exported {path.name}"
        )


class RankingTab:
    def __init__(
        self,
        parent,
        *,
        app,
    ):
        self.app = app
        self.rows = []

        self.preset = preset_for(
            "ranking"
        )

        self.start_address_var = tk.StringVar(
            value=self.preset["start"]
        )
        self.end_address_var = tk.StringVar(
            value=self.preset["end"]
        )
        self.candidate_count_var = tk.IntVar(
            value=int(
                self.preset.get(
                    "candidate_count",
                    5,
                )
            )
        )
        self.traffic_count_var = tk.IntVar(
            value=int(
                self.preset.get(
                    "traffic_count",
                    40,
                )
            )
        )
        self.random_seed_var = tk.IntVar(
            value=int(
                self.preset.get(
                    "seed",
                    7,
                )
            )
        )

        root = ttk.Frame(
            parent,
            padding=10,
        )
        root.pack(
            fill="both",
            expand=True,
        )

        ttk.Label(
            root,
            text="8. Full Candidate-Route Ranking",
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            root,
            text=(
                "Select candidate routes for the same A/B endpoints. The balanced "
                "score normalizes travel time, battery energy and relative SiC "
                "damage; lower is better."
            ),
            wraplength=1180,
        ).pack(
            anchor="w",
            pady=(3, 8),
        )

        generator = ttk.LabelFrame(
            root,
            text="Generate complete A-to-B route-ranking experiment",
            padding=7,
        )
        generator.pack(
            fill="x",
            pady=(0, 8),
        )

        ttk.Label(
            generator,
            text="A:",
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Entry(
            generator,
            textvariable=self.start_address_var,
            width=48,
        ).grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(5, 10),
        )

        ttk.Label(
            generator,
            text="B:",
        ).grid(
            row=0,
            column=4,
            sticky="w",
        )
        ttk.Entry(
            generator,
            textvariable=self.end_address_var,
            width=48,
        ).grid(
            row=0,
            column=5,
            columnspan=3,
            sticky="ew",
            padx=(5, 10),
        )

        ttk.Label(
            generator,
            text="Candidates:",
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(7, 0),
        )
        ttk.Spinbox(
            generator,
            from_=2,
            to=12,
            textvariable=self.candidate_count_var,
            width=7,
        ).grid(
            row=1,
            column=1,
            sticky="w",
            pady=(7, 0),
        )

        ttk.Label(
            generator,
            text="Traffic:",
        ).grid(
            row=1,
            column=2,
            sticky="e",
            padx=(8, 4),
            pady=(7, 0),
        )
        ttk.Spinbox(
            generator,
            from_=0,
            to=300,
            textvariable=self.traffic_count_var,
            width=7,
        ).grid(
            row=1,
            column=3,
            sticky="w",
            pady=(7, 0),
        )

        ttk.Label(
            generator,
            text="Seed:",
        ).grid(
            row=1,
            column=4,
            sticky="e",
            padx=(8, 4),
            pady=(7, 0),
        )
        ttk.Spinbox(
            generator,
            from_=0,
            to=999999,
            textvariable=self.random_seed_var,
            width=8,
        ).grid(
            row=1,
            column=5,
            sticky="w",
            pady=(7, 0),
        )

        self.generate_rank_button = ttk.Button(
            generator,
            text="Generate + Run + Rank",
            command=self.generate_and_rank,
        )
        self.generate_rank_button.grid(
            row=1,
            column=7,
            sticky="e",
            padx=(10, 0),
            pady=(7, 0),
        )

        generator.columnconfigure(
            1,
            weight=1,
        )
        generator.columnconfigure(
            5,
            weight=1,
        )

        top = ttk.Panedwindow(
            root,
            orient="horizontal",
        )
        top.pack(
            fill="both",
            expand=False,
        )

        select_frame = ttk.LabelFrame(
            top,
            text="Candidate profiles",
            padding=6,
        )
        weights_frame = ttk.LabelFrame(
            top,
            text="Balanced-route weights",
            padding=8,
        )

        top.add(
            select_frame,
            weight=3,
        )
        top.add(
            weights_frame,
            weight=1,
        )

        self.profile_list = tk.Listbox(
            select_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            height=8,
            font=("Consolas", 8),
        )
        self.profile_list.pack(
            fill="both",
            expand=True,
        )

        self.time_weight = tk.DoubleVar(
            value=1.0
        )
        self.energy_weight = tk.DoubleVar(
            value=1.0
        )
        self.damage_weight = tk.DoubleVar(
            value=1.0
        )

        for row_index, (
            label,
            variable,
        ) in enumerate(
            (
                ("Travel time", self.time_weight),
                ("Battery energy", self.energy_weight),
                ("SiC damage", self.damage_weight),
            )
        ):
            ttk.Label(
                weights_frame,
                text=label + ":",
            ).grid(
                row=row_index,
                column=0,
                sticky="w",
                pady=4,
            )

            ttk.Spinbox(
                weights_frame,
                from_=0.0,
                to=10.0,
                increment=0.25,
                textvariable=variable,
                width=8,
            ).grid(
                row=row_index,
                column=1,
                sticky="w",
                padx=(8, 0),
                pady=4,
            )

        controls = ttk.Frame(root)
        controls.pack(
            fill="x",
            pady=(8, 6),
        )

        self.analyze_button = ttk.Button(
            controls,
            text="Analyze and rank selected",
            command=self.analyze,
        )
        self.analyze_button.pack(
            side="left",
        )

        ttk.Button(
            controls,
            text="Select all",
            command=self.select_all,
        ).pack(
            side="left",
            padx=(8, 0),
        )

        ttk.Button(
            controls,
            text="Refresh profiles",
            command=self.app.refresh_profiles,
        ).pack(
            side="left",
            padx=(8, 0),
        )

        self.export_button = ttk.Button(
            controls,
            text="Export ranking CSV",
            command=self.export,
            state="disabled",
        )
        self.export_button.pack(
            side="left",
            padx=(8, 0),
        )

        self.status_var = tk.StringVar(
            value="Select at least two candidate routes."
        )

        ttk.Label(
            controls,
            textvariable=self.status_var,
        ).pack(
            side="right",
        )

        frame = ttk.LabelFrame(
            root,
            text="Ranked routes",
            padding=5,
        )
        frame.pack(
            fill="both",
            expand=True,
        )

        columns = (
            "rank",
            "profile",
            "score",
            "time",
            "distance",
            "ascent",
            "energy",
            "whkm",
            "peak_tj",
            "delta_tj",
            "damage",
        )

        self.tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
        )

        headings = {
            "rank": "Rank",
            "profile": "Profile",
            "score": "Balanced score",
            "time": "Time s",
            "distance": "km",
            "ascent": "Ascent m",
            "energy": "Battery kWh",
            "whkm": "Wh/km",
            "peak_tj": "Peak Tj C",
            "delta_tj": "Max DeltaTj C",
            "damage": "Relative damage",
        }

        for column in columns:
            self.tree.heading(
                column,
                text=headings[column],
            )
            self.tree.column(
                column,
                width=(
                    245
                    if column == "profile"
                    else 105
                ),
                anchor=(
                    "w"
                    if column == "profile"
                    else "center"
                ),
            )

        self.tree.pack(
            fill="both",
            expand=True,
        )

        self.winners_var = tk.StringVar(
            value=""
        )

        ttk.Label(
            root,
            textvariable=self.winners_var,
            font=("Segoe UI", 9, "bold"),
        ).pack(
            anchor="w",
            pady=(8, 0),
        )

    def generate_and_rank(
        self,
    ):
        start = self.start_address_var.get().strip()
        end = self.end_address_var.get().strip()

        if not start or not end:
            messagebox.showinfo(
                "Route ranking",
                "Enter both Stuttgart addresses.",
            )
            return

        params = {
            "project_root": self.app.project_root,
            "vehicle_config_path": self.app.vehicle_config,
            "cycles_dir": self.app.cycles_dir,
            "start_address": start,
            "end_address": end,
            "candidate_count": int(
                self.candidate_count_var.get()
            ),
            "traffic_count": int(
                self.traffic_count_var.get()
            ),
            "random_seed": int(
                self.random_seed_var.get()
            ),
        }

        weights = (
            float(
                self.time_weight.get()
            ),
            float(
                self.energy_weight.get()
            ),
            float(
                self.damage_weight.get()
            ),
        )

        self.app.run_background(
            kind=("ranking", self),
            function=self._generate_rank_worker,
            args=(params, weights),
            status=(
                "Generating candidate routes, running fixed-step "
                "missions and ranking them..."
            ),
        )

    def _generate_rank_worker(
        self,
        params,
        weights,
    ):
        generated = generate_ranking_experiment(
            **params
        )

        rows = analyze_group(
            generated.all_paths,
            self.app.vehicle_config,
        )

        ranked = normalized_balanced_ranking(
            rows,
            time_weight=weights[0],
            energy_weight=weights[1],
            damage_weight=weights[2],
        )

        return {
            "ranked_rows": ranked,
            "generated_paths": list(
                generated.all_paths
            ),
            "generation_notes": list(
                generated.notes
            ),
        }

    def refresh(
        self,
        profile_rows,
    ):
        selected = {
            self.profile_list.get(index)
            for index in self.profile_list.curselection()
        }

        self.profile_list.delete(
            0,
            tk.END,
        )

        for row in profile_rows:
            self.profile_list.insert(
                tk.END,
                row["name"],
            )

        for index, row in enumerate(
            profile_rows
        ):
            if row["name"] in selected:
                self.profile_list.selection_set(
                    index
                )

    def select_all(self):
        self.profile_list.selection_set(
            0,
            tk.END,
        )

    def analyze(self):
        indices = self.profile_list.curselection()

        if len(indices) < 2:
            messagebox.showinfo(
                "Route ranking",
                "Select at least two candidate-route profiles.",
            )
            return

        paths = [
            self.app.profile_rows[index]["path"]
            for index in indices
        ]

        weights = (
            float(
                self.time_weight.get()
            ),
            float(
                self.energy_weight.get()
            ),
            float(
                self.damage_weight.get()
            ),
        )

        if sum(weights) <= 0.0:
            messagebox.showinfo(
                "Route ranking",
                "At least one ranking weight must be greater than zero.",
            )
            return

        self.app.run_background(
            kind=("ranking", self),
            function=self._worker,
            args=(paths, weights),
            status="Running full route ranking...",
        )

    def _worker(
        self,
        paths,
        weights,
    ):
        rows = analyze_group(
            paths,
            self.app.vehicle_config,
        )

        return normalized_balanced_ranking(
            rows,
            time_weight=weights[0],
            energy_weight=weights[1],
            damage_weight=weights[2],
        )

    def receive_result(
        self,
        rows,
    ):
        notes = ()

        if isinstance(
            rows,
            dict,
        ):
            notes = rows.get(
                "generation_notes",
                (),
            )
            generated_paths = rows.get(
                "generated_paths",
                (),
            )
            rows = rows["ranked_rows"]

            self.app.refresh_profiles()

            wanted = {
                str(
                    Path(path).resolve()
                )
                for path in generated_paths
            }

            self.profile_list.selection_clear(
                0,
                tk.END,
            )

            for index, profile in enumerate(
                self.app.profile_rows
            ):
                if (
                    str(
                        Path(
                            profile["path"]
                        ).resolve()
                    )
                    in wanted
                ):
                    self.profile_list.selection_set(
                        index
                    )

        self.rows = rows

        for item in self.tree.get_children():
            self.tree.delete(
                item
            )

        for row in rows:
            self.tree.insert(
                "",
                tk.END,
                values=(
                    row["balanced_rank"],
                    row["profile_name"],
                    f"{float(row['balanced_score']):.4f}",
                    f"{float(row['duration_s']):.1f}",
                    f"{float(row['distance_km']):.3f}",
                    f"{float(row['ascent_m']):.1f}",
                    f"{float(row['net_dc_energy_kwh']):.4f}",
                    f"{float(row['wh_per_km']):.1f}",
                    f"{float(row['peak_junction_temperature_c']):.2f}",
                    f"{float(row['maximum_delta_tj_c']):.2f}",
                    f"{float(row['total_relative_damage']):.3e}",
                ),
            )

        fastest = min(
            rows,
            key=lambda row: row["duration_s"],
        )
        energy = min(
            rows,
            key=lambda row: row["net_dc_energy_kwh"],
        )
        damage = min(
            rows,
            key=lambda row: row["total_relative_damage"],
        )
        balanced = rows[0]

        self.winners_var.set(
            (
                f"Fastest: {fastest['profile_name']}   |   "
                f"Lowest energy: {energy['profile_name']}   |   "
                f"Lowest damage: {damage['profile_name']}   |   "
                f"Balanced: {balanced['profile_name']}"
            )
        )

        note_text = (
            " | " + " ".join(notes)
            if notes
            else ""
        )

        self.status_var.set(
            f"Ranked {len(rows)} candidate routes.{note_text}"
        )

        self.export_button.configure(
            state="normal",
        )

    def receive_error(
        self,
        exc,
    ):
        self.status_var.set(
            "Ranking failed."
        )
        messagebox.showerror(
            "Route ranking failed",
            str(exc),
        )

    def export(self):
        if not self.rows:
            return

        try:
            path = export_experiment_csv(
                experiment_name="full_candidate_route_ranking",
                rows=self.rows,
                output_dir=(
                    self.app.export_dir
                    / "route_experiments"
                ),
            )
        except Exception as exc:
            messagebox.showerror(
                "Export failed",
                str(exc),
            )
            return

        self.status_var.set(
            f"Exported {path.name}"
        )


class ExperimentLab:
    def __init__(
        self,
        root,
        *,
        cycles_dir,
        vehicle_config,
        export_dir,
    ):
        self.root = root
        self.cycles_dir = Path(
            cycles_dir
        )
        self.project_root = (
            self.cycles_dir
            .resolve()
            .parent
        )
        self.vehicle_config = Path(
            vehicle_config
        )
        self.export_dir = Path(
            export_dir
        )

        self.profile_rows = []
        self.tabs = []
        self.executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="route-experiment-lab",
        )
        self.future = None
        self.future_kind = None

        root.title(
            "Route Experiment Lab - Stuttgart SiC Route Optimization"
        )
        root.geometry(
            "1420x840"
        )
        root.minsize(
            1050,
            680,
        )

        self.status_var = tk.StringVar(
            value="Loading drive-cycle profiles..."
        )

        self._build_ui()
        self.refresh_profiles()
        self._poll_future()

    def _build_ui(self):
        toolbar = ttk.Frame(
            self.root,
            padding=(10, 9, 10, 5),
        )
        toolbar.pack(
            fill="x"
        )

        ttk.Label(
            toolbar,
            text="ROUTE EXPERIMENT LAB",
            font=("Segoe UI", 15, "bold"),
        ).pack(
            side="left"
        )

        ttk.Label(
            toolbar,
            text=(
                "F8 | Full A-to-B route research experiments"
            ),
        ).pack(
            side="left",
            padx=(12, 0),
        )

        ttk.Button(
            toolbar,
            text="Open cycles folder",
            command=lambda: self.open_path(
                self.cycles_dir
            ),
        ).pack(
            side="right",
        )

        ttk.Button(
            toolbar,
            text="Refresh all profiles",
            command=self.refresh_profiles,
        ).pack(
            side="right",
            padx=(0, 8),
        )

        notebook = ttk.Notebook(
            self.root
        )
        notebook.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=(0, 8),
        )

        for spec in PAIR_EXPERIMENTS:
            frame = ttk.Frame(
                notebook
            )
            notebook.add(
                frame,
                text=spec["title"],
            )

            tab = PairExperimentTab(
                frame,
                spec=spec,
                app=self,
            )
            self.tabs.append(
                tab
            )

        ranking_frame = ttk.Frame(
            notebook
        )
        notebook.add(
            ranking_frame,
            text="8. Route Ranking",
        )

        self.ranking_tab = RankingTab(
            ranking_frame,
            app=self,
        )

        status = ttk.Frame(
            self.root,
            padding=(10, 0, 10, 8),
        )
        status.pack(
            fill="x"
        )

        self.progress = ttk.Progressbar(
            status,
            mode="indeterminate",
            length=170,
        )
        self.progress.pack(
            side="left",
        )

        ttk.Label(
            status,
            textvariable=self.status_var,
        ).pack(
            side="left",
            padx=(10, 0),
        )

        ttk.Label(
            status,
            text=(
                f"Vehicle: {self.vehicle_config.name}"
            ),
        ).pack(
            side="right",
        )

    def refresh_profiles(self):
        self.profile_rows = discover_profiles(
            self.cycles_dir
        )

        for tab in self.tabs:
            tab.refresh(
                self.profile_rows
            )

        self.ranking_tab.refresh(
            self.profile_rows
        )

        self.status_var.set(
            f"Loaded {len(self.profile_rows)} raw drive-cycle profile(s)."
        )

    def run_background(
        self,
        *,
        kind,
        function,
        args,
        status,
    ):
        if (
            self.future is not None
            and not self.future.done()
        ):
            messagebox.showinfo(
                "Experiment Lab busy",
                "Wait for the current analysis to finish.",
            )
            return

        self.future_kind = kind
        self.future = self.executor.submit(
            function,
            *args,
        )

        self.status_var.set(
            status
        )
        self.progress.start(
            12
        )

    def _poll_future(self):
        if (
            self.future is not None
            and self.future.done()
        ):
            future = self.future
            kind = self.future_kind

            self.future = None
            self.future_kind = None
            self.progress.stop()

            try:
                result = future.result()
            except Exception as exc:
                target = (
                    kind[1]
                    if isinstance(
                        kind,
                        tuple,
                    )
                    else None
                )

                if target is not None:
                    target.receive_error(
                        exc
                    )
                else:
                    messagebox.showerror(
                        "Experiment analysis failed",
                        str(exc),
                    )

                self.status_var.set(
                    "Analysis failed."
                )
            else:
                target = (
                    kind[1]
                    if isinstance(
                        kind,
                        tuple,
                    )
                    else None
                )

                if target is not None:
                    target.receive_result(
                        result
                    )

                self.status_var.set(
                    "Experiment analysis complete."
                )

        self.root.after(
            150,
            self._poll_future,
        )

    @staticmethod
    def open_path(path):
        path = str(
            Path(path).resolve()
        )

        if sys.platform.startswith(
            "win"
        ):
            os.startfile(
                path
            )
        elif sys.platform == "darwin":
            subprocess.Popen(
                [
                    "open",
                    path,
                ]
            )
        else:
            subprocess.Popen(
                [
                    "xdg-open",
                    path,
                ]
            )

    def close(self):
        self.executor.shutdown(
            wait=False,
            cancel_futures=True,
        )
        self.root.destroy()


def parse_args():
    parser = argparse.ArgumentParser()

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


def main():
    args = parse_args()

    root = tk.Tk()

    app = ExperimentLab(
        root,
        cycles_dir=args.cycles_dir,
        vehicle_config=args.vehicle_config,
        export_dir=args.export_dir,
    )

    root.protocol(
        "WM_DELETE_WINDOW",
        app.close,
    )

    root.mainloop()


if __name__ == "__main__":
    main()
