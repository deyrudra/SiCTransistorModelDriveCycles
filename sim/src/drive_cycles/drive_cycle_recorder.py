from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DriveCycleSample:
    time_s: float
    speed_mps: float
    grade_deg: float


class DriveCycleRecorder:
    def __init__(
        self,
        vehicle_id: int,
        *,
        output_dir: str | Path,
        route_node_count: int = 0,
        route_distance_m: float = 0.0,
        sample_interval_s: float = 0.05,
    ) -> None:
        self.vehicle_id = int(vehicle_id)
        self.output_dir = Path(output_dir)
        self.route_node_count = int(route_node_count)
        self.route_distance_m = float(route_distance_m)
        self.sample_interval_s = max(1e-6, float(sample_interval_s))

        self.samples: list[DriveCycleSample] = []
        self.started = False
        self.saved = False

        self.start_sim_time_s: Optional[float] = None
        self.previous_elapsed_s: Optional[float] = None
        self.previous_speed_mps: Optional[float] = None
        self.previous_grade_deg: Optional[float] = None

        self.next_sample_time_s = 0.0
        self.saved_path: Optional[Path] = None

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def duration_s(self) -> float:
        if not self.samples:
            return 0.0
        return self.samples[-1].time_s

    def start(
        self,
        simulation_time_s: float,
        *,
        initial_speed_mps: float = 0.0,
        grade_deg: float = 0.0,
    ) -> None:
        if self.started:
            return

        now = float(simulation_time_s)
        self.started = True
        self.start_sim_time_s = now

        self.previous_elapsed_s = 0.0
        self.previous_speed_mps = max(0.0, float(initial_speed_mps))
        self.previous_grade_deg = float(grade_deg)

        self.samples.append(
            DriveCycleSample(
                time_s=0.0,
                speed_mps=self.previous_speed_mps,
                grade_deg=self.previous_grade_deg,
            )
        )
        self.next_sample_time_s = self.sample_interval_s

    def record(
        self,
        *,
        simulation_time_s: float,
        speed_mps: float,
        grade_deg: float,
        force: bool = False,
    ) -> bool:
        if self.saved:
            return False

        if not self.started:
            self.start(
                simulation_time_s,
                initial_speed_mps=speed_mps,
                grade_deg=grade_deg,
            )
            return True

        assert self.start_sim_time_s is not None
        assert self.previous_elapsed_s is not None
        assert self.previous_speed_mps is not None
        assert self.previous_grade_deg is not None

        current_elapsed = max(
            0.0,
            float(simulation_time_s) - self.start_sim_time_s,
        )
        current_speed = max(0.0, float(speed_mps))
        current_grade = float(grade_deg)

        if current_elapsed < self.previous_elapsed_s:
            return False

        emitted = False
        interval = current_elapsed - self.previous_elapsed_s

        while self.next_sample_time_s <= current_elapsed + 1e-12:
            target_t = self.next_sample_time_s

            if interval <= 1e-12:
                alpha = 1.0
            else:
                alpha = (
                    target_t - self.previous_elapsed_s
                ) / interval
                alpha = max(0.0, min(1.0, alpha))

            interp_speed = (
                self.previous_speed_mps
                + alpha * (current_speed - self.previous_speed_mps)
            )
            interp_grade = (
                self.previous_grade_deg
                + alpha * (current_grade - self.previous_grade_deg)
            )

            self.samples.append(
                DriveCycleSample(
                    time_s=target_t,
                    speed_mps=max(0.0, interp_speed),
                    grade_deg=interp_grade,
                )
            )

            self.next_sample_time_s += self.sample_interval_s
            emitted = True

        self.previous_elapsed_s = current_elapsed
        self.previous_speed_mps = current_speed
        self.previous_grade_deg = current_grade

        if force:
            emitted = (
                self._append_final_state_if_needed(
                    current_elapsed,
                    current_speed,
                    current_grade,
                )
                or emitted
            )

        return emitted

    def _append_final_state_if_needed(
        self,
        elapsed_s: float,
        speed_mps: float,
        grade_deg: float,
    ) -> bool:
        if not self.samples:
            return False

        last = self.samples[-1]
        if abs(last.time_s - elapsed_s) <= 1e-9:
            return False

        self.samples.append(
            DriveCycleSample(
                time_s=elapsed_s,
                speed_mps=max(0.0, float(speed_mps)),
                grade_deg=float(grade_deg),
            )
        )
        return True

    def save(
        self,
        *,
        status: str,
        extra_metadata: Optional[dict[str, object]] = None,
    ) -> Optional[Path]:
        if self.saved:
            return self.saved_path
        if not self.samples:
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.output_dir / (
            f"drive_cycle_{timestamp}_veh{self.vehicle_id}.csv"
        )

        metadata = {
            "vehicle_id": self.vehicle_id,
            "dt": self.sample_interval_s,
            "rows": len(self.samples),
            "duration_s": self.duration_s,
            "status": status,
            "route_node_count": self.route_node_count,
            "route_distance_m": self.route_distance_m,
            "grade_source": "caller_supplied",
            "sampling_mode": "fixed_grid_linear_interpolation",
        }

        if extra_metadata:
            metadata.update(extra_metadata)

        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(
                "# drive_cycle recorded "
                + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                + "\n"
            )
            for key, value in metadata.items():
                handle.write(f"# {key}={value}\n")

            writer = csv.writer(handle)
            writer.writerow(["time_s", "v_mps", "grade_deg"])

            for sample in self.samples:
                writer.writerow(
                    [
                        f"{sample.time_s:.6f}",
                        f"{sample.speed_mps:.6f}",
                        f"{sample.grade_deg:.6f}",
                    ]
                )

        self.saved = True
        self.saved_path = path

        print(
            f"[drive-cycle] saved {path} "
            f"({len(self.samples)} samples, "
            f"{self.duration_s:.2f} s, status={status})"
        )

        return path
