from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class VehicleDynamicsConfig:
    name: str
    mass_kg: float
    frontal_area_m2: float
    drag_coefficient: float
    rolling_resistance_coefficient: float
    drivetrain_efficiency: float
    regenerative_efficiency: float
    max_propulsion_power_w: float
    max_regen_power_w: float
    max_acceleration_mps2: float
    comfortable_braking_mps2: float
    emergency_braking_mps2: float
    safe_distance_m: float
    time_headway_s: float


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML section {name!r} must be a mapping.")
    return value


def _number(
    section: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = section.get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Configuration value {key!r} must be numeric, got {raw!r}."
        ) from exc

    if minimum is not None and value < minimum:
        raise ValueError(f"{key!r} must be >= {minimum}, got {value}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{key!r} must be <= {maximum}, got {value}.")

    return value


def load_vehicle_config(path: str | Path) -> VehicleDynamicsConfig:
    config_path = Path(path).expanduser().resolve()

    if not config_path.is_file():
        raise FileNotFoundError(f"Vehicle config not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError("Vehicle YAML root must be a mapping.")

    vehicle = _section(data, "vehicle")
    powertrain = _section(data, "powertrain")
    driver = _section(data, "driver")

    name = str(vehicle.get("name", config_path.stem)).strip() or config_path.stem

    return VehicleDynamicsConfig(
        name=name,
        mass_kg=_number(vehicle, "mass_kg", 1800.0, minimum=100.0),
        frontal_area_m2=_number(vehicle, "frontal_area_m2", 2.2, minimum=0.1),
        drag_coefficient=_number(vehicle, "drag_coefficient", 0.25, minimum=0.01),
        rolling_resistance_coefficient=_number(
            vehicle, "rolling_resistance_coefficient", 0.010, minimum=0.0
        ),
        drivetrain_efficiency=_number(
            powertrain, "drivetrain_efficiency", 0.92, minimum=0.01, maximum=1.0
        ),
        regenerative_efficiency=_number(
            powertrain, "regenerative_efficiency", 0.80, minimum=0.0, maximum=1.0
        ),
        max_propulsion_power_w=_number(
            powertrain, "max_propulsion_power_w", 200000.0, minimum=0.0
        ),
        max_regen_power_w=_number(
            powertrain, "max_regen_power_w", 80000.0, minimum=0.0
        ),
        max_acceleration_mps2=_number(
            powertrain, "max_acceleration_mps2", 1.8, minimum=0.1
        ),
        comfortable_braking_mps2=_number(
            powertrain, "comfortable_braking_mps2", 2.5, minimum=0.1
        ),
        emergency_braking_mps2=_number(
            powertrain, "emergency_braking_mps2", 6.0, minimum=0.1
        ),
        safe_distance_m=_number(driver, "safe_distance_m", 5.0, minimum=0.0),
        time_headway_s=_number(driver, "time_headway_s", 1.5, minimum=0.1),
    )
