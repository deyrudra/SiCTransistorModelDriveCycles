from __future__ import annotations

import argparse
import csv
import math
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Allow direct execution from sim/src/drive_cycles.
THIS_DIR = Path(__file__).resolve().parent
SRC_DIR = THIS_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from drive_cycles.vehicle_route_validation import (
    validate_vehicle_route,
    save_vehicle_route_validation,
)
from drive_cycles.wltc_class3_validation import (
    UNECE_WLTC_WORKBOOK_URL,
    download_official_workbook,
    extract_class3_trace_from_xls,
    run_wltc_validation,
    summarize_trace,
)
from drive_cycles.vehicle_config_calibration import (
    CalibrationParameters,
    backup_and_activate_yaml,
    load_calibration_parameters,
    write_calibrated_yaml,
)
from drive_cycles.inverter_validation import (
    run_inverter_mission_validation,
    validate_cab525f12xm3_datasheet,
)
from drive_cycles.thermal_validation import (
    run_thermal_mission_validation,
    validate_cab525f12xm3_thermal,
)
from drive_cycles.reliability_validation import (
    run_reliability_mission_validation,
    validate_reliability_model,
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
        self.wltc_tab = ttk.Frame(notebook)
        self.inverter_tab = ttk.Frame(notebook)
        self.thermal_tab = ttk.Frame(notebook)
        self.reliability_tab = ttk.Frame(notebook)

        notebook.add(self.vehicle_tab, text="1. Vehicle / Route")
        notebook.add(self.wltc_tab, text="2. WLTC Class 3")
        notebook.add(self.inverter_tab, text="3. Inverter")
        notebook.add(self.thermal_tab, text="4. Thermal")
        notebook.add(self.reliability_tab, text="5. Reliability")

        self._build_vehicle_tab()
        self._build_wltc_tab()
        self._build_inverter_tab()
        self._build_thermal_tab()
        self._build_reliability_tab()

    def _build_wltc_tab(self) -> None:
        root = ttk.Frame(
            self.wltc_tab,
            padding=16,
        )
        root.pack(
            fill="both",
            expand=True,
        )

        ttk.Label(
            root,
            text="WLTC Class 3 standardized vehicle validation",
            font=("TkDefaultFont", 13, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            root,
            text=(
                "The target speed trace is prescribed directly to the "
                "longitudinal vehicle model on 0 degree grade. Traffic, map "
                "routing and Stuttgart elevation are intentionally bypassed."
            ),
            wraplength=1050,
        ).pack(
            anchor="w",
            pady=(4, 12),
        )

        source_frame = ttk.LabelFrame(
            root,
            text="Official UNECE trace source",
            padding=10,
        )
        source_frame.pack(
            fill="x",
        )

        self.wltc_workbook_var = tk.StringVar(
            value=str(
                self.export_dir
                / "wltc"
                / "WLTP-DHC-12-07e.xls"
            )
        )

        self.wltc_trace_var = tk.StringVar(
            value=str(
                self.export_dir
                / "wltc"
                / "wltc_class3_official.csv"
            )
        )

        ttk.Label(
            source_frame,
            text="UNECE source:",
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )

        ttk.Label(
            source_frame,
            text=UNECE_WLTC_WORKBOOK_URL,
            wraplength=900,
        ).grid(
            row=0,
            column=1,
            columnspan=4,
            sticky="w",
            padx=(8, 0),
        )

        ttk.Label(
            source_frame,
            text="Workbook:",
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Entry(
            source_frame,
            textvariable=self.wltc_workbook_var,
            width=92,
        ).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(8, 6),
            pady=(8, 0),
        )

        ttk.Button(
            source_frame,
            text="Import .xls...",
            command=self._browse_wltc_workbook,
        ).grid(
            row=1,
            column=2,
            padx=4,
            pady=(8, 0),
        )

        ttk.Button(
            source_frame,
            text="Download official",
            command=self._download_wltc_workbook,
        ).grid(
            row=1,
            column=3,
            padx=4,
            pady=(8, 0),
        )

        ttk.Button(
            source_frame,
            text="Extract trace",
            command=self._extract_wltc_trace,
        ).grid(
            row=1,
            column=4,
            padx=(4, 0),
            pady=(8, 0),
        )

        ttk.Label(
            source_frame,
            text="Trace CSV:",
        ).grid(
            row=2,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Entry(
            source_frame,
            textvariable=self.wltc_trace_var,
            width=92,
        ).grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(8, 6),
            pady=(8, 0),
        )

        ttk.Button(
            source_frame,
            text="Select CSV...",
            command=self._browse_wltc_trace,
        ).grid(
            row=2,
            column=2,
            padx=4,
            pady=(8, 0),
        )

        ttk.Button(
            source_frame,
            text="Run WLTC validation",
            command=self._run_wltc_validation,
        ).grid(
            row=2,
            column=3,
            columnspan=2,
            padx=(4, 0),
            pady=(8, 0),
            sticky="ew",
        )

        source_frame.columnconfigure(
            1,
            weight=1,
        )

        calibration_frame = ttk.LabelFrame(
            root,
            text="Vehicle energy calibration parameters",
            padding=10,
        )
        calibration_frame.pack(
            fill="x",
            pady=(12, 0),
        )

        self.calibration_vars = {
            "crr": tk.StringVar(),
            "drive_eff": tk.StringVar(),
            "regen_eff": tk.StringVar(),
            "base_aux_w": tk.StringVar(),
        }

        calibration_fields = [
            (
                "Rolling resistance Crr",
                "crr",
                "Typical model input; higher = more tire loss.",
            ),
            (
                "Drivetrain efficiency",
                "drive_eff",
                "Battery/DC to wheel efficiency during propulsion.",
            ),
            (
                "Regenerative efficiency",
                "regen_eff",
                "Wheel braking energy converted back to DC energy.",
            ),
            (
                "Base auxiliary power (W)",
                "base_aux_w",
                "Always-on battery-side electrical load.",
            ),
        ]

        for row_index, (
            label_text,
            key,
            help_text,
        ) in enumerate(calibration_fields):
            ttk.Label(
                calibration_frame,
                text=label_text + ":",
            ).grid(
                row=row_index,
                column=0,
                sticky="w",
                pady=2,
            )

            ttk.Entry(
                calibration_frame,
                textvariable=self.calibration_vars[key],
                width=14,
            ).grid(
                row=row_index,
                column=1,
                sticky="w",
                padx=(8, 12),
                pady=2,
            )

            ttk.Label(
                calibration_frame,
                text=help_text,
                foreground="#666666",
            ).grid(
                row=row_index,
                column=2,
                sticky="w",
                pady=2,
            )

        button_row = ttk.Frame(
            calibration_frame
        )
        button_row.grid(
            row=0,
            column=3,
            rowspan=4,
            sticky="ns",
            padx=(18, 0),
        )

        ttk.Button(
            button_row,
            text="Reload current YAML",
            command=self._load_calibration_editor,
        ).pack(
            fill="x",
            pady=(0, 6),
        )

        ttk.Button(
            button_row,
            text="Test edited values",
            command=self._test_edited_wltc_values,
        ).pack(
            fill="x",
            pady=6,
        )

        ttk.Button(
            button_row,
            text="Export / make active",
            command=self._export_calibrated_yaml,
        ).pack(
            fill="x",
            pady=6,
        )

        ttk.Label(
            calibration_frame,
            text=(
                "Testing uses a temporary YAML. Exporting first backs up "
                "the active YAML into car_configs/old/ with a timestamp."
            ),
            foreground="#555555",
            wraplength=900,
        ).grid(
            row=4,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(8, 0),
        )

        calibration_frame.columnconfigure(
            2,
            weight=1,
        )

        self._load_calibration_editor()

        results_frame = ttk.LabelFrame(
            root,
            text="WLTC Class 3 result",
            padding=10,
        )
        results_frame.pack(
            fill="both",
            expand=True,
            pady=(12, 0),
        )

        self.wltc_result_text = tk.Text(
            results_frame,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
        )
        self.wltc_result_text.pack(
            fill="both",
            expand=True,
        )

        self.wltc_status_var = tk.StringVar(
            value=(
                "Prepare the official UNECE trace, then run the "
                "standardized validation."
            )
        )

        ttk.Label(
            root,
            textvariable=self.wltc_status_var,
            anchor="w",
        ).pack(
            fill="x",
            pady=(8, 0),
        )

    def _load_calibration_editor(self) -> None:
        try:
            params = load_calibration_parameters(
                self.vehicle_config
            )
        except Exception as exc:
            messagebox.showerror(
                "Vehicle calibration",
                f"Could not read the active YAML:\n\n{exc}",
            )
            return

        self.calibration_vars["crr"].set(
            f"{params.rolling_resistance_coefficient:.6f}"
        )
        self.calibration_vars["drive_eff"].set(
            f"{params.drivetrain_efficiency:.6f}"
        )
        self.calibration_vars["regen_eff"].set(
            f"{params.regenerative_efficiency:.6f}"
        )
        self.calibration_vars["base_aux_w"].set(
            f"{params.base_auxiliary_power_w:.1f}"
        )

    def _calibration_parameters_from_editor(
        self,
    ) -> CalibrationParameters:
        try:
            return CalibrationParameters(
                rolling_resistance_coefficient=float(
                    self.calibration_vars["crr"].get()
                ),
                drivetrain_efficiency=float(
                    self.calibration_vars["drive_eff"].get()
                ),
                regenerative_efficiency=float(
                    self.calibration_vars["regen_eff"].get()
                ),
                base_auxiliary_power_w=float(
                    self.calibration_vars["base_aux_w"].get()
                ),
            )
        except ValueError as exc:
            raise ValueError(
                "All four calibration fields must contain valid numbers."
            ) from exc

    def _temporary_calibration_yaml_path(
        self,
    ) -> Path:
        return (
            self.export_dir
            / "wltc"
            / "temporary_calibration_vehicle.yaml"
        )

    def _test_edited_wltc_values(self) -> None:
        trace_path = Path(
            self.wltc_trace_var.get()
        )

        if not trace_path.is_file():
            messagebox.showinfo(
                "WLTC Class 3",
                "Prepare or select the official Class 3 trace CSV first.",
            )
            return

        try:
            params = self._calibration_parameters_from_editor()
            temporary_yaml = write_calibrated_yaml(
                self.vehicle_config,
                self._temporary_calibration_yaml_path(),
                params,
            )
        except Exception as exc:
            messagebox.showerror(
                "Calibration values",
                str(exc),
            )
            return

        self._run_wltc_validation_with_config(
            temporary_yaml,
            config_label="TEMPORARY EDITED VALUES",
        )

    def _export_calibrated_yaml(self) -> None:
        try:
            params = self._calibration_parameters_from_editor()
        except Exception as exc:
            messagebox.showerror(
                "Calibration values",
                str(exc),
            )
            return

        confirmation = messagebox.askyesno(
            "Export calibrated vehicle YAML",
            (
                "This will make the edited values active.\n\n"
                f"Active file:\n{self.vehicle_config}\n\n"
                "The current file will first be copied into an 'old' "
                "folder with a timestamp.\n\n"
                "Continue?"
            ),
        )

        if not confirmation:
            return

        try:
            backup_path, active_path = backup_and_activate_yaml(
                self.vehicle_config,
                params,
            )
        except Exception as exc:
            messagebox.showerror(
                "YAML export failed",
                str(exc),
            )
            return

        self._load_calibration_editor()

        self.wltc_status_var.set(
            f"New active YAML saved; backup: {backup_path.name}"
        )

        messagebox.showinfo(
            "Vehicle YAML updated",
            (
                f"New active YAML:\n{active_path}\n\n"
                f"Previous YAML archived as:\n{backup_path}"
            ),
        )

    def _browse_wltc_workbook(self) -> None:
        path = filedialog.askopenfilename(
            title="Select official UNECE WLTC workbook",
            filetypes=[
                ("Excel 97-2003 workbook", "*.xls"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.wltc_workbook_var.set(
                path
            )

    def _browse_wltc_trace(self) -> None:
        path = filedialog.askopenfilename(
            title="Select WLTC Class 3 trace CSV",
            filetypes=[
                ("CSV files", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.wltc_trace_var.set(
                path
            )

    def _download_wltc_workbook(self) -> None:
        destination = Path(
            self.wltc_workbook_var.get()
        )

        self.wltc_status_var.set(
            "Downloading official UNECE workbook..."
        )
        self.update_idletasks()

        try:
            path = download_official_workbook(
                destination
            )
        except Exception as exc:
            messagebox.showerror(
                "WLTC download failed",
                (
                    f"{exc}\n\n"
                    "You can download the UNECE workbook in your browser "
                    "and use 'Import .xls...' instead."
                ),
            )
            self.wltc_status_var.set(
                "Download failed; manual import is available."
            )
            return

        self.wltc_workbook_var.set(
            str(path)
        )
        self.wltc_status_var.set(
            f"Downloaded: {path.name}"
        )

    def _extract_wltc_trace(self) -> None:
        workbook = Path(
            self.wltc_workbook_var.get()
        )
        output = Path(
            self.wltc_trace_var.get()
        )

        if not workbook.is_file():
            messagebox.showinfo(
                "WLTC Class 3",
                "Select or download the official UNECE .xls workbook first.",
            )
            return

        self.wltc_status_var.set(
            "Extracting and verifying WLTC Class 3 trace..."
        )
        self.update_idletasks()

        try:
            path = extract_class3_trace_from_xls(
                workbook,
                output,
            )
            summary = summarize_trace(
                path
            )
        except Exception as exc:
            messagebox.showerror(
                "WLTC extraction failed",
                str(exc),
            )
            self.wltc_status_var.set(
                "Trace extraction failed."
            )
            return

        self.wltc_trace_var.set(
            str(path)
        )

        self.wltc_status_var.set(
            (
                f"Trace ready: {summary.distance_km:.3f} km, "
                f"{summary.duration_s:.0f} s, "
                f"peak {summary.peak_speed_kmh:.1f} km/h"
            )
        )

    def _run_wltc_validation(self) -> None:
        self._run_wltc_validation_with_config(
            self.vehicle_config,
            config_label="ACTIVE YAML",
        )

    def _run_wltc_validation_with_config(
        self,
        config_path: Path,
        *,
        config_label: str,
    ) -> None:
        trace_path = Path(
            self.wltc_trace_var.get()
        )

        if not trace_path.is_file():
            messagebox.showinfo(
                "WLTC Class 3",
                "Prepare or select the official Class 3 trace CSV first.",
            )
            return

        self.wltc_status_var.set(
            f"Running WLTC with {config_label.lower()}..."
        )
        self.update_idletasks()

        try:
            result = run_wltc_validation(
                trace_path,
                config_path,
            )
        except Exception as exc:
            messagebox.showerror(
                "WLTC validation failed",
                str(exc),
            )
            self.wltc_status_var.set(
                "WLTC validation failed."
            )
            return

        lines = [
            "WLTC CLASS 3 STANDARDIZED VEHICLE VALIDATION",
            "=" * 70,
            "",
            "TEST DEFINITION",
            "  Speed input:      official UNECE Class 3 target trace",
            "  Road grade:       0.0 deg",
            "  Traffic:          disabled",
            "  Route elevation:  disabled",
            "  Config mode:      " + config_label,
            "  Vehicle config:   " + Path(config_path).name,
            "",
            "TRACE VERIFICATION",
            f"  Samples:          {result.trace.sample_count}",
            f"  Duration:         {result.trace.duration_s:.1f} s",
            f"  Distance:         {result.trace.distance_km:.3f} km",
            f"  Average speed:    {result.trace.average_speed_kmh:.2f} km/h",
            f"  Peak speed:       {result.trace.peak_speed_kmh:.2f} km/h",
            f"  Stopped time:     {result.trace.stopped_time_percent:.2f} %",
            f"  Speed checksum:   {result.trace.speed_checksum_kmh:.1f}",
            f"  Trace assessment: {result.trace_assessment}",
            "",
            "BATTERY ENERGY",
            f"  Traction:         {result.traction_energy_kwh:.6f} kWh",
            f"  Recovered regen:  {result.recovered_energy_kwh:.6f} kWh",
            f"  Base auxiliary:   {result.base_auxiliary_energy_kwh:.6f} kWh",
            f"  HVAC:             {result.hvac_energy_kwh:.6f} kWh",
            f"  Total auxiliary:  {result.total_auxiliary_energy_kwh:.6f} kWh",
            f"  Net battery:      {result.net_battery_energy_kwh:.6f} kWh",
            "",
            "GERMAN WLTP BENCHMARK COMPARISON",
            f"  Benchmark:        {result.benchmark_name}",
            f"  Official target:  {result.benchmark_wh_per_km:.2f} Wh/km",
            f"  Simulation:       {result.simulated_wh_per_km:.2f} Wh/km",
            f"  Difference:       {result.error_wh_per_km:+.2f} Wh/km",
            f"  Error:            {result.error_percent:+.2f} %",
            f"  Assessment:       {result.energy_assessment}",
            "",
            "INTERPRETATION",
            (
                "  This comparison is far stronger than the Stuttgart-route "
                "comparison because"
            ),
            (
                "  the simulated vehicle and the benchmark are now evaluated "
                "against a standardized"
            ),
            (
                "  flat prescribed-speed cycle rather than different route "
                "topography and traffic."
            ),
        ]

        self.wltc_result_text.configure(
            state="normal"
        )
        self.wltc_result_text.delete(
            "1.0",
            tk.END,
        )
        self.wltc_result_text.insert(
            "1.0",
            "\n".join(lines),
        )
        self.wltc_result_text.configure(
            state="disabled"
        )

        self.wltc_status_var.set(
            (
                f"WLTC validation complete: "
                f"{result.simulated_wh_per_km:.2f} Wh/km "
                f"({result.error_percent:+.2f} %)"
            )
        )

    def _build_inverter_tab(self) -> None:
        root = ttk.Frame(
            self.inverter_tab,
            padding=14,
        )
        root.pack(
            fill="both",
            expand=True,
        )

        ttk.Label(
            root,
            text="Wolfspeed CAB525F12XM3 inverter validation",
            font=("TkDefaultFont", 13, "bold"),
        ).pack(
            anchor="w"
        )

        ttk.Label(
            root,
            text=(
                "Stage 1 checks that the electrical model reproduces the "
                "published CAB525F12XM3 reference points. Stage 2 runs the "
                "same inverter model over the WLTC Class 3 mission profile."
            ),
            wraplength=1050,
        ).pack(
            anchor="w",
            pady=(4, 12),
        )

        controls = ttk.Frame(
            root
        )
        controls.pack(
            fill="x",
            pady=(0, 10),
        )

        ttk.Button(
            controls,
            text="1. Validate datasheet points",
            command=self._run_inverter_datasheet_validation,
        ).pack(
            side="left",
        )

        ttk.Button(
            controls,
            text="2. Run WLTC inverter mission",
            command=self._run_inverter_wltc_validation,
        ).pack(
            side="left",
            padx=(8, 0),
        )

        self.inverter_status_var = tk.StringVar(
            value=(
                "Run the datasheet validation first."
            )
        )

        ttk.Label(
            controls,
            textvariable=self.inverter_status_var,
        ).pack(
            side="right",
        )

        panes = ttk.Panedwindow(
            root,
            orient="vertical",
        )
        panes.pack(
            fill="both",
            expand=True,
        )

        reference_frame = ttk.LabelFrame(
            panes,
            text="Datasheet reference-point reproduction",
            padding=6,
        )
        mission_frame = ttk.LabelFrame(
            panes,
            text="WLTC inverter mission result",
            padding=6,
        )

        panes.add(
            reference_frame,
            weight=2,
        )
        panes.add(
            mission_frame,
            weight=2,
        )

        columns = (
            "parameter",
            "datasheet",
            "model",
            "error",
            "status",
        )

        self.inverter_reference_tree = ttk.Treeview(
            reference_frame,
            columns=columns,
            show="headings",
            height=10,
        )

        headings = {
            "parameter": "Parameter",
            "datasheet": "Datasheet",
            "model": "Model",
            "error": "Error %",
            "status": "Status",
        }

        widths = {
            "parameter": 240,
            "datasheet": 145,
            "model": 145,
            "error": 100,
            "status": 90,
        }

        for column in columns:
            self.inverter_reference_tree.heading(
                column,
                text=headings[column],
            )
            self.inverter_reference_tree.column(
                column,
                width=widths[column],
                anchor=(
                    "w"
                    if column == "parameter"
                    else "center"
                ),
            )

        self.inverter_reference_tree.pack(
            fill="both",
            expand=True,
        )

        self.inverter_mission_text = tk.Text(
            mission_frame,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
            height=12,
        )
        self.inverter_mission_text.pack(
            fill="both",
            expand=True,
        )

    @staticmethod
    def _format_inverter_reference_value(
        value: float,
        unit: str,
    ) -> str:
        if unit == "ohm":
            return (
                f"{value * 1000.0:.4f} mOhm"
            )
        if unit == "J":
            return (
                f"{value * 1000.0:.3f} mJ"
            )
        if unit == "A":
            return f"{value:.1f} A"
        if unit == "V":
            return f"{value:.1f} V"
        return f"{value:.6g} {unit}"

    def _run_inverter_datasheet_validation(
        self,
    ) -> None:
        try:
            result = validate_cab525f12xm3_datasheet(
                self.vehicle_config
            )
        except Exception as exc:
            messagebox.showerror(
                "Inverter validation failed",
                str(exc),
            )
            self.inverter_status_var.set(
                "Datasheet validation failed."
            )
            return

        for item in (
            self.inverter_reference_tree.get_children()
        ):
            self.inverter_reference_tree.delete(
                item
            )

        for check in result.checks:
            self.inverter_reference_tree.insert(
                "",
                "end",
                values=(
                    check.name,
                    self._format_inverter_reference_value(
                        check.datasheet_value,
                        check.unit,
                    ),
                    self._format_inverter_reference_value(
                        check.model_value,
                        check.unit,
                    ),
                    f"{check.error_percent:+.3f}",
                    "PASS" if check.passed else "FAIL",
                ),
            )

        self.inverter_status_var.set(
            (
                f"{result.device_name}: "
                + (
                    "DATASHEET PASS"
                    if result.overall_pass
                    else "DATASHEET FAIL"
                )
            )
        )

    def _run_inverter_wltc_validation(
        self,
    ) -> None:
        trace_path = Path(
            self.wltc_trace_var.get()
        )

        if not trace_path.is_file():
            messagebox.showinfo(
                "Inverter WLTC validation",
                (
                    "Prepare the WLTC Class 3 trace in tab 2 "
                    "before running the inverter mission."
                ),
            )
            return

        self.inverter_status_var.set(
            "Running WLTC inverter mission..."
        )
        self.update_idletasks()

        try:
            result = run_inverter_mission_validation(
                trace_path,
                self.vehicle_config,
            )
        except Exception as exc:
            messagebox.showerror(
                "Inverter WLTC validation failed",
                str(exc),
            )
            self.inverter_status_var.set(
                "WLTC inverter mission failed."
            )
            return

        lines = [
            "WLTC CLASS 3 INVERTER MISSION VALIDATION",
            "=" * 66,
            "",
            "REFERENCE DEVICE",
            "  Wolfspeed CAB525F12XM3",
            "  1200 V SiC half-bridge power module",
            "",
            "MISSION ELECTRICAL RESULTS",
            (
                f"  Peak phase current:      "
                f"{result.peak_phase_current_a:.2f} A"
            ),
            (
                f"  Peak device current:     "
                f"{result.peak_device_current_a:.2f} A"
            ),
            (
                f"  Peak inverter loss:      "
                f"{result.peak_total_loss_w:.2f} W"
            ),
            (
                f"  Conduction loss energy:  "
                f"{result.conduction_energy_wh:.3f} Wh"
            ),
            (
                f"  Switching loss energy:   "
                f"{result.switching_energy_wh:.3f} Wh"
            ),
            (
                f"  Total inverter loss:     "
                f"{result.loss_energy_wh:.3f} Wh"
            ),
            (
                f"  Switching share:         "
                f"{result.switching_fraction_percent:.2f} %"
            ),
            (
                f"  Peak unserved power:     "
                f"{result.peak_unserved_power_w:.2f} W"
            ),
            (
                "  Current-limit status:    "
                + (
                    "PASS"
                    if result.served_without_current_limit
                    else "REVIEW - current limit reached"
                )
            ),
            "",
            "MODEL STATUS",
            (
                "  RDS(on) and switching-energy reference values are "
                "datasheet anchored."
            ),
            (
                "  Current/voltage scaling away from the published "
                "reference point remains a first-order approximation."
            ),
            (
                "  Thermal Foster parameters are validated separately "
                "in the next stage."
            ),
        ]

        self.inverter_mission_text.configure(
            state="normal"
        )
        self.inverter_mission_text.delete(
            "1.0",
            tk.END,
        )
        self.inverter_mission_text.insert(
            "1.0",
            "\n".join(lines),
        )
        self.inverter_mission_text.configure(
            state="disabled"
        )

        self.inverter_status_var.set(
            (
                "WLTC inverter mission complete: "
                f"{result.loss_energy_wh:.2f} Wh loss"
            )
        )

    def _build_thermal_tab(self) -> None:
        root = ttk.Frame(
            self.thermal_tab,
            padding=14,
        )
        root.pack(
            fill="both",
            expand=True,
        )

        ttk.Label(
            root,
            text="Wolfspeed CAB525F12XM3 thermal validation",
            font=("TkDefaultFont", 13, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            root,
            text=(
                "The Foster network represents the complete junction-to-fluid "
                "thermal path. The hard steady-state anchor is Rth,J-F = "
                "0.145 C/W at 4 L/min; transient points are approximate "
                "readings from datasheet Figure 17."
            ),
            wraplength=1050,
        ).pack(
            anchor="w",
            pady=(4, 12),
        )

        controls = ttk.Frame(root)
        controls.pack(
            fill="x",
            pady=(0, 10),
        )

        ttk.Button(
            controls,
            text="1. Validate thermal impedance",
            command=self._run_thermal_datasheet_validation,
        ).pack(side="left")

        ttk.Button(
            controls,
            text="2. Run WLTC thermal mission",
            command=self._run_thermal_wltc_validation,
        ).pack(
            side="left",
            padx=(8, 0),
        )

        self.thermal_status_var = tk.StringVar(
            value="Run thermal-impedance validation first."
        )

        ttk.Label(
            controls,
            textvariable=self.thermal_status_var,
        ).pack(side="right")

        panes = ttk.Panedwindow(
            root,
            orient="vertical",
        )
        panes.pack(
            fill="both",
            expand=True,
        )

        zth_frame = ttk.LabelFrame(
            panes,
            text="CAB525F12XM3 Zth,J-F reproduction",
            padding=6,
        )
        mission_frame = ttk.LabelFrame(
            panes,
            text="WLTC thermal mission result",
            padding=6,
        )

        panes.add(
            zth_frame,
            weight=2,
        )
        panes.add(
            mission_frame,
            weight=2,
        )

        columns = (
            "time",
            "datasheet",
            "model",
            "error",
            "status",
        )

        self.thermal_zth_tree = ttk.Treeview(
            zth_frame,
            columns=columns,
            show="headings",
            height=10,
        )

        headings = {
            "time": "Pulse time",
            "datasheet": "Figure 17 / Datasheet",
            "model": "Foster model",
            "error": "Error %",
            "status": "Status",
        }

        for column in columns:
            self.thermal_zth_tree.heading(
                column,
                text=headings[column],
            )
            self.thermal_zth_tree.column(
                column,
                width=(
                    180
                    if column in (
                        "datasheet",
                        "model",
                    )
                    else 120
                ),
                anchor="center",
            )

        self.thermal_zth_tree.pack(
            fill="both",
            expand=True,
        )

        self.thermal_mission_text = tk.Text(
            mission_frame,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
            height=12,
        )
        self.thermal_mission_text.pack(
            fill="both",
            expand=True,
        )

    def _run_thermal_datasheet_validation(
        self,
    ) -> None:
        try:
            result = validate_cab525f12xm3_thermal(
                self.vehicle_config
            )
        except Exception as exc:
            messagebox.showerror(
                "Thermal validation failed",
                str(exc),
            )
            self.thermal_status_var.set(
                "Thermal validation failed."
            )
            return

        for item in self.thermal_zth_tree.get_children():
            self.thermal_zth_tree.delete(item)

        self.thermal_zth_tree.insert(
            "",
            "end",
            values=(
                "steady state",
                (
                    f"{result.datasheet_steady_rth_c_per_w:.6f} C/W"
                ),
                (
                    f"{result.model_steady_rth_c_per_w:.6f} C/W"
                ),
                f"{result.steady_error_percent:+.2f}",
                "PASS" if result.steady_pass else "FAIL",
            ),
        )

        for check in result.transient_checks:
            self.thermal_zth_tree.insert(
                "",
                "end",
                values=(
                    f"{check.time_s:.6g} s",
                    f"{check.datasheet_zth_c_per_w:.6f} C/W",
                    f"{check.model_zth_c_per_w:.6f} C/W",
                    f"{check.error_percent:+.2f}",
                    "PASS" if check.passed else "FAIL",
                ),
            )

        self.thermal_status_var.set(
            (
                f"{result.device_name}: "
                + (
                    "THERMAL IMPEDANCE PASS"
                    if result.overall_pass
                    else "THERMAL IMPEDANCE REVIEW"
                )
            )
        )

    def _run_thermal_wltc_validation(
        self,
    ) -> None:
        trace_path = Path(
            self.wltc_trace_var.get()
        )

        if not trace_path.is_file():
            messagebox.showinfo(
                "Thermal WLTC validation",
                (
                    "Prepare the WLTC Class 3 trace in tab 2 "
                    "before running the thermal mission."
                ),
            )
            return

        self.thermal_status_var.set(
            "Running WLTC electro-thermal mission..."
        )
        self.update_idletasks()

        try:
            result = run_thermal_mission_validation(
                trace_path,
                self.vehicle_config,
            )
        except Exception as exc:
            messagebox.showerror(
                "Thermal WLTC validation failed",
                str(exc),
            )
            self.thermal_status_var.set(
                "WLTC thermal mission failed."
            )
            return

        lines = [
            "WLTC CLASS 3 THERMAL MISSION VALIDATION",
            "=" * 66,
            "",
            "THERMAL BOUNDARY",
            "  Device:                   Wolfspeed CAB525F12XM3",
            (
                f"  Fluid temperature:       "
                f"{result.fluid_temperature_c:.2f} C"
            ),
            "  Flow-rate reference:      4.0 L/min per module",
            "  Datasheet Rth,J-F:         0.145 C/W",
            "",
            "MISSION RESULTS",
            (
                f"  Peak phase current:       "
                f"{result.peak_phase_current_a:.2f} A"
            ),
            (
                f"  Peak aggregate loss:      "
                f"{result.peak_aggregate_loss_w:.2f} W"
            ),
            (
                f"  Peak loss per position:   "
                f"{result.peak_device_loss_w:.2f} W"
            ),
            (
                f"  Peak junction temp:       "
                f"{result.peak_junction_temperature_c:.2f} C"
            ),
            (
                f"  Peak Tj rise over fluid:  "
                f"{result.peak_delta_tj_c:.2f} C"
            ),
            (
                f"  Total inverter loss:      "
                f"{result.total_loss_energy_wh:.3f} Wh"
            ),
            (
                f"  Non-converged samples:    "
                f"{result.nonconverged_samples}"
            ),
            (
                f"  Over-temperature samples: "
                f"{result.overtemperature_samples}"
            ),
            "",
            "ASSESSMENT",
            (
                "  Solver convergence:       "
                + (
                    "PASS"
                    if result.solver_pass
                    else "FAIL"
                )
            ),
            (
                "  Tj <= 175 C:              "
                + (
                    "PASS"
                    if result.temperature_pass
                    else "FAIL"
                )
            ),
            "",
            "MODEL STATUS",
            (
                "  Steady-state junction-to-fluid resistance is directly "
                "anchored to the datasheet."
            ),
            (
                "  The transient Foster network is a fitted representation "
                "of Figure 17, not a manufacturer-supplied RC table."
            ),
        ]

        self.thermal_mission_text.configure(
            state="normal"
        )
        self.thermal_mission_text.delete(
            "1.0",
            tk.END,
        )
        self.thermal_mission_text.insert(
            "1.0",
            "\n".join(lines),
        )
        self.thermal_mission_text.configure(
            state="disabled"
        )

        self.thermal_status_var.set(
            (
                "WLTC thermal mission complete: "
                f"peak Tj {result.peak_junction_temperature_c:.2f} C"
            )
        )

    def _build_reliability_tab(self) -> None:
        root = ttk.Frame(
            self.reliability_tab,
            padding=14,
        )
        root.pack(
            fill="both",
            expand=True,
        )

        ttk.Label(
            root,
            text="CAB525F12XM3 mission-profile reliability",
            font=("TkDefaultFont", 13, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            root,
            text=(
                "This stage validates the damage-model behaviour and applies "
                "rainflow + Miner accumulation to the electro-thermal mission. "
                "Because CAB525F12XM3 cycles-to-failure coefficients are not "
                "publicly available, results remain relative durability "
                "indices rather than absolute lifetime."
            ),
            wraplength=1080,
        ).pack(
            anchor="w",
            pady=(4, 12),
        )

        controls = ttk.Frame(root)
        controls.pack(
            fill="x",
            pady=(0, 10),
        )

        ttk.Button(
            controls,
            text="1. Validate damage model",
            command=self._run_reliability_model_validation,
        ).pack(side="left")

        ttk.Button(
            controls,
            text="2. Run WLTC reliability mission",
            command=self._run_reliability_wltc_validation,
        ).pack(
            side="left",
            padx=(8, 0),
        )

        self.reliability_status_var = tk.StringVar(
            value="Run damage-model validation first."
        )

        ttk.Label(
            controls,
            textvariable=self.reliability_status_var,
        ).pack(side="right")

        panes = ttk.Panedwindow(
            root,
            orient="vertical",
        )
        panes.pack(
            fill="both",
            expand=True,
        )

        checks_frame = ttk.LabelFrame(
            panes,
            text="Reliability model qualification",
            padding=6,
        )
        mission_frame = ttk.LabelFrame(
            panes,
            text="WLTC reliability mission result",
            padding=6,
        )

        panes.add(
            checks_frame,
            weight=2,
        )
        panes.add(
            mission_frame,
            weight=2,
        )

        columns = (
            "check",
            "status",
            "detail",
        )

        self.reliability_checks_tree = ttk.Treeview(
            checks_frame,
            columns=columns,
            show="headings",
            height=7,
        )

        self.reliability_checks_tree.heading(
            "check",
            text="Check",
        )
        self.reliability_checks_tree.heading(
            "status",
            text="Status",
        )
        self.reliability_checks_tree.heading(
            "detail",
            text="Detail",
        )

        self.reliability_checks_tree.column(
            "check",
            width=320,
            anchor="w",
        )
        self.reliability_checks_tree.column(
            "status",
            width=90,
            anchor="center",
        )
        self.reliability_checks_tree.column(
            "detail",
            width=620,
            anchor="w",
        )

        self.reliability_checks_tree.pack(
            fill="both",
            expand=True,
        )

        self.reliability_mission_text = tk.Text(
            mission_frame,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
            height=14,
        )
        self.reliability_mission_text.pack(
            fill="both",
            expand=True,
        )

    def _run_reliability_model_validation(
        self,
    ) -> None:
        try:
            result = validate_reliability_model(
                self.vehicle_config
            )
        except Exception as exc:
            messagebox.showerror(
                "Reliability validation failed",
                str(exc),
            )
            self.reliability_status_var.set(
                "Reliability validation failed."
            )
            return

        for item in self.reliability_checks_tree.get_children():
            self.reliability_checks_tree.delete(
                item
            )

        for check in result.checks:
            self.reliability_checks_tree.insert(
                "",
                "end",
                values=(
                    check.name,
                    "PASS" if check.passed else "FAIL",
                    check.detail,
                ),
            )

        qualification = (
            "RELATIVE MODEL PASS"
            if result.overall_pass
            else "MODEL REVIEW"
        )

        self.reliability_status_var.set(
            qualification
            + " | absolute CAB525 lifetime: NOT CALIBRATED"
        )

    def _run_reliability_wltc_validation(
        self,
    ) -> None:
        trace_path = Path(
            self.wltc_trace_var.get()
        )

        if not trace_path.is_file():
            messagebox.showinfo(
                "Reliability WLTC validation",
                (
                    "Prepare the WLTC Class 3 trace in tab 2 "
                    "before running the reliability mission."
                ),
            )
            return

        self.reliability_status_var.set(
            "Running WLTC rainflow and relative-damage analysis..."
        )
        self.update_idletasks()

        try:
            result = run_reliability_mission_validation(
                trace_path,
                self.vehicle_config,
            )
        except Exception as exc:
            messagebox.showerror(
                "Reliability WLTC validation failed",
                str(exc),
            )
            self.reliability_status_var.set(
                "WLTC reliability mission failed."
            )
            return

        total_envelope_cycles = (
            result.cycles_inside_manufacturer_pc_temperature_envelope
            + result.cycles_outside_manufacturer_pc_temperature_envelope
        )

        inside_percent = (
            100.0
            * result.cycles_inside_manufacturer_pc_temperature_envelope
            / total_envelope_cycles
            if total_envelope_cycles > 0.0
            else 0.0
        )

        lines = [
            "WLTC CLASS 3 RELIABILITY MISSION ANALYSIS",
            "=" * 68,
            "",
            "METHOD",
            "  Thermal history:        CAB525F12XM3 electro-thermal model",
            "  Cycle extraction:       rainflow counting",
            "  Damage accumulation:    Palmgren-Miner linear accumulation",
            "  Stress variables:       Delta Tj, Tj,max, excursion duration",
            "",
            "MISSION DAMAGE",
            (
                f"  Relative damage index:  "
                f"{result.total_relative_damage:.6e}"
            ),
            (
                f"  Equivalent cycles:      "
                f"{result.equivalent_full_cycles:.2f}"
            ),
            (
                f"  Max cycle contribution: "
                f"{result.maximum_damage_contribution:.6e}"
            ),
            (
                f"  Most damaging cycle:    "
                f"{result.most_damaging_cycle_index}"
            ),
            "",
            "DAMAGE-WEIGHTED STRESS",
            (
                f"  Delta Tj:               "
                f"{result.damage_weighted_delta_tj_c:.2f} C"
            ),
            (
                f"  Tj,max:                 "
                f"{result.damage_weighted_tjmax_c:.2f} C"
            ),
            (
                f"  Excursion duration:     "
                f"{result.damage_weighted_duration_s:.2f} s"
            ),
            "",
            "WOLFSPEED POWER-CYCLING CONTEXT",
            (
                f"  Inside published PC temperature envelope: "
                f"{result.cycles_inside_manufacturer_pc_temperature_envelope:.2f} "
                f"cycles ({inside_percent:.1f} %)"
            ),
            (
                f"  Outside published PC temperature envelope: "
                f"{result.cycles_outside_manufacturer_pc_temperature_envelope:.2f}"
            ),
            (
                f"  PCsec-like durations:   "
                f"{result.pcsec_equivalent_cycles:.2f}"
            ),
            (
                f"  Transition durations:   "
                f"{result.transition_duration_equivalent_cycles:.2f}"
            ),
            (
                f"  PCmin-like durations:   "
                f"{result.pcmin_equivalent_cycles:.2f}"
            ),
            "",
            "QUALIFICATION",
            "  Route-to-route relative ranking: ENABLED",
            "  CAB525 absolute cycles-to-failure: NOT CALIBRATED",
            "  Years of life / remaining useful life: NOT CLAIMED",
            "",
            (
                "  The manufacturer publishes the power-cycling methodology "
                "and typical stress"
            ),
            (
                "  ranges, but not CAB525F12XM3-specific life-model "
                "coefficients. Relative"
            ),
            (
                "  damage is therefore the scientifically defensible output "
                "for this model."
            ),
        ]

        self.reliability_mission_text.configure(
            state="normal"
        )
        self.reliability_mission_text.delete(
            "1.0",
            tk.END,
        )
        self.reliability_mission_text.insert(
            "1.0",
            "\n".join(lines),
        )
        self.reliability_mission_text.configure(
            state="disabled"
        )

        self.reliability_status_var.set(
            (
                "WLTC reliability complete: relative damage "
                f"{result.total_relative_damage:.3e}"
            )
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
            orient="vertical",
        )
        lower.pack(fill="both", expand=False, pady=(8, 0))

        diagnostic_row = ttk.Panedwindow(
            lower,
            orient="horizontal",
        )

        breakdown_frame = ttk.LabelFrame(
            diagnostic_row,
            text="Energy breakdown",
            padding=6,
        )
        elevation_frame = ttk.LabelFrame(
            diagnostic_row,
            text="Elevation / grade validation",
            padding=6,
        )
        detail_frame = ttk.LabelFrame(
            lower,
            text="Selected result details",
            padding=6,
        )

        diagnostic_row.add(breakdown_frame, weight=1)
        diagnostic_row.add(elevation_frame, weight=1)

        lower.add(diagnostic_row, weight=2)
        lower.add(detail_frame, weight=2)

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

        self.regen_efficiency_var = tk.StringVar(
            value="Regen capture efficiency: -"
        )
        ttk.Label(
            breakdown_frame,
            textvariable=self.regen_efficiency_var,
            font=("TkDefaultFont", 9, "bold"),
        ).pack(anchor="w", pady=(5, 0))

        ttk.Label(
            breakdown_frame,
            text=(
                "Capture efficiency = recovered DC energy / available wheel braking energy. "
                "Inertial and grade terms are signed. Auxiliary loads are battery-side "
                "and are not passed through the traction inverter loss model."
            ),
            foreground="#666666",
            wraplength=430,
        ).pack(anchor="w", pady=(5, 0))

        self.elevation = ttk.Treeview(
            elevation_frame,
            columns=("metric", "value"),
            show="headings",
            height=11,
        )
        self.elevation.heading("metric", text="Elevation / grade metric")
        self.elevation.heading("value", text="Value")
        self.elevation.column("metric", width=260, anchor="w")
        self.elevation.column("value", width=145, anchor="e")
        self.elevation.pack(fill="both", expand=True)

        ttk.Label(
            elevation_frame,
            text=(
                "DEM endpoint difference should broadly agree with the "
                "grade-integrated net elevation change. Grade-guard hits "
                "show where the smoothed profile still reaches the configured "
                "physical road-grade limit."
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
        self.regen_efficiency_var.set(
            f"Regen capture efficiency: "
            f"{result.regen_capture_efficiency_percent:.2f} %"
        )

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
                "Positive wheel traction energy",
                result.positive_wheel_traction_energy_kwh,
            ),
            (
                "Available braking energy",
                result.negative_wheel_energy_kwh,
            ),
            (
                "Energy recovered by regen",
                -result.recovered_regen_energy_breakdown_kwh,
            ),
            (
                "Energy lost to friction braking",
                result.friction_brake_energy_breakdown_kwh,
            ),
            (
                "Base auxiliary electrical energy",
                result.base_auxiliary_energy_kwh,
            ),
            (
                "HVAC energy",
                result.hvac_energy_kwh,
            ),
            (
                "Total auxiliary electrical energy",
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

    @staticmethod
    def _optional_m(value) -> str:
        return "N/A" if value is None else f"{value:+.1f} m"

    def _populate_elevation(self, result) -> None:
        for item in self.elevation.get_children():
            self.elevation.delete(item)

        rows = [
            (
                "Start elevation (local DEM)",
                (
                    "N/A"
                    if result.start_elevation_m is None
                    else f"{result.start_elevation_m:.1f} m"
                ),
            ),
            (
                "End elevation (local DEM)",
                (
                    "N/A"
                    if result.end_elevation_m is None
                    else f"{result.end_elevation_m:.1f} m"
                ),
            ),
            (
                "Endpoint DEM difference",
                self._optional_m(result.endpoint_dem_delta_m),
            ),
            (
                "Grade-integrated net change",
                f"{result.grade_integrated_net_elevation_change_m:+.1f} m",
            ),
            (
                "Total ascent",
                f"{result.total_ascent_m:.1f} m",
            ),
            (
                "Total descent",
                f"{result.total_descent_m:.1f} m",
            ),
            (
                "Maximum uphill grade",
                f"{result.max_uphill_grade_deg:+.3f} deg",
            ),
            (
                "Maximum downhill grade",
                f"{result.max_downhill_grade_deg:+.3f} deg",
            ),
            (
                "Mean absolute grade",
                f"{result.mean_abs_grade_deg:.3f} deg",
            ),
            (
                "Distance-weighted |grade|",
                f"{result.distance_weighted_mean_abs_grade_deg:.3f} deg",
            ),
            (
                "Physical grade guard",
                f"+/-{result.grade_guard_deg:.1f} deg",
            ),
            (
                "Samples at grade guard",
                (
                    f"{result.grade_clamp_sample_count} "
                    f"({result.grade_clamp_sample_percent:.3f} %)"
                ),
            ),
            (
                "Grade-guard status",
                result.grade_guard_status,
            ),
            (
                "Elevation smoothing radius",
                f"{result.elevation_smoothing_radius_m:.1f} m",
            ),
            (
                "Grade calculation baseline",
                f"{result.elevation_grade_baseline_m:.1f} m",
            ),
            (
                "Profile vs endpoint delta error",
                self._optional_m(
                    result.elevation_profile_consistency_error_m
                ),
            ),
        ]

        for metric, value in rows:
            self.elevation.insert(
                "",
                "end",
                values=(metric, value),
            )

    def _show_selected_result(self, _event=None) -> None:
        selected = self.results.selection()
        if not selected:
            return

        result = self.result_by_item.get(selected[0])
        if result is None:
            return

        self._populate_breakdown(result)
        self._populate_elevation(result)

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
            f"  Available braking:     {result.negative_wheel_energy_kwh:+.6f} kWh",
            f"  Recovered regen:       {-result.recovered_regen_energy_breakdown_kwh:+.6f} kWh",
            f"  Friction braking:      {result.friction_brake_energy_breakdown_kwh:+.6f} kWh",
            f"  Regen capture eff.:    {result.regen_capture_efficiency_percent:.2f} %",
            f"  Base auxiliary:        {result.base_auxiliary_energy_kwh:+.6f} kWh",
            f"  HVAC:                  {result.hvac_energy_kwh:+.6f} kWh",
            f"  Total auxiliary:       {result.auxiliary_energy_kwh:+.6f} kWh",
            f"  Active aux power:      {result.auxiliary_power_w:.0f} W",
            f"  Net battery estimate:  {result.net_battery_energy_breakdown_kwh:+.6f} kWh",
            f"  Ledger consumption:    {result.breakdown_wh_per_km:.2f} Wh/km",
            f"  Ledger - model net DC: {ledger_difference_kwh:+.6f} kWh",
            "",
            "ELEVATION / GRADE",
            (
                "  Start elevation:       N/A"
                if result.start_elevation_m is None
                else f"  Start elevation:       {result.start_elevation_m:.1f} m"
            ),
            (
                "  End elevation:         N/A"
                if result.end_elevation_m is None
                else f"  End elevation:         {result.end_elevation_m:.1f} m"
            ),
            (
                "  Endpoint DEM delta:    N/A"
                if result.endpoint_dem_delta_m is None
                else f"  Endpoint DEM delta:    {result.endpoint_dem_delta_m:+.1f} m"
            ),
            f"  Integrated net change: {result.grade_integrated_net_elevation_change_m:+.1f} m",
            f"  Total ascent:          {result.total_ascent_m:.1f} m",
            f"  Total descent:         {result.total_descent_m:.1f} m",
            f"  Max uphill grade:      {result.max_uphill_grade_deg:+.3f} deg",
            f"  Max downhill grade:    {result.max_downhill_grade_deg:+.3f} deg",
            f"  Mean absolute grade:   {result.mean_abs_grade_deg:.3f} deg",
            f"  Physical grade guard:  +/-{result.grade_guard_deg:.1f} deg",
            (
                f"  Grade-guard hits:      "
                f"{result.grade_clamp_sample_count} "
                f"({result.grade_clamp_sample_percent:.3f} %)"
            ),
            f"  Guard status:           {result.grade_guard_status}",
            f"  Smoothing radius:       {result.elevation_smoothing_radius_m:.1f} m",
            f"  Grade baseline:         {result.elevation_grade_baseline_m:.1f} m",
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
