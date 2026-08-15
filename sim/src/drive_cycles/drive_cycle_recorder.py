from __future__ import annotations

"""
Raw drive-cycle recorder for the selected ego vehicle.

Output schema intentionally matches the previous MissionTwin pipeline:

    time_s,v_mps,grade_deg

Acceleration is not written here; it should be derived later from the validated
speed trace so filtering/resampling stays centralized in the analysis pipeline.
"""

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

        self.sample_interval_s = max(
            1e-6,
            float(sample_interval_s),
        )

        self.samples: list[DriveCycleSample] = []

        self.started = False
        self.saved = False

        self.start_sim_time_s: Optional[float] = None
        self.last_sample_time_s: Optional[float] = None

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
        self.last_sample_time_s = None

        self.record(
            simulation_time_s=now,
            speed_mps=initial_speed_mps,
            grade_deg=grade_deg,
            force=True,
        )

    def record(
        self,
        *,
        simulation_time_s: float,
        speed_mps: float,
        grade_deg: float,
        force: bool = False,
    ) -> bool:
        """
        Add one sample when the requested fixed sampling interval is due.

        The simulation may render at a different rate; samples are timestamped
        by simulation time rather than wall-clock time.
        """

        if self.saved:
            return False

        if not self.started:
            self.start(
                simulation_time_s,
                initial_speed_mps=speed_mps,
                grade_deg=grade_deg,
            )
            return True

        sim_time = float(simulation_time_s)

        assert self.start_sim_time_s is not None

        elapsed = max(
            0.0,
            sim_time - self.start_sim_time_s,
        )

        if (
            not force
            and self.last_sample_time_s is not None
            and elapsed - self.last_sample_time_s
            < self.sample_interval_s - 1e-9
        ):
            return False

        sample = DriveCycleSample(
            time_s=elapsed,
            speed_mps=max(0.0, float(speed_mps)),
            grade_deg=float(grade_deg),
        )

        self.samples.append(sample)
        self.last_sample_time_s = elapsed

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

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

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
        }

        if extra_metadata:
            metadata.update(extra_metadata)

        with path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            handle.write(
                "# drive_cycle recorded "
                + datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                + "\n"
            )

            for key, value in metadata.items():
                handle.write(
                    f"# {key}={value}\n"
                )

            writer = csv.writer(handle)

            writer.writerow(
                [
                    "time_s",
                    "v_mps",
                    "grade_deg",
                ]
            )

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
            f"{self.duration_s:.2f} s, "
            f"status={status})"
        )

        return path
