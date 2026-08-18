from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil

import yaml


@dataclass(frozen=True)
class CalibrationParameters:
    rolling_resistance_coefficient: float
    drivetrain_efficiency: float
    regenerative_efficiency: float
    base_auxiliary_power_w: float


def load_calibration_parameters(
    yaml_path: str | Path,
) -> CalibrationParameters:
    path = Path(yaml_path)

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = yaml.safe_load(handle) or {}

    vehicle = data.get("vehicle", {})
    powertrain = data.get("powertrain", {})
    auxiliary = data.get("auxiliary", {})

    return CalibrationParameters(
        rolling_resistance_coefficient=float(
            vehicle.get(
                "rolling_resistance_coefficient",
                0.010,
            )
        ),
        drivetrain_efficiency=float(
            powertrain.get(
                "drivetrain_efficiency",
                0.92,
            )
        ),
        regenerative_efficiency=float(
            powertrain.get(
                "regenerative_efficiency",
                0.80,
            )
        ),
        base_auxiliary_power_w=float(
            auxiliary.get(
                "base_power_w",
                300.0,
            )
        ),
    )


def validate_calibration_parameters(
    parameters: CalibrationParameters,
) -> None:
    if not (
        0.001
        <= parameters.rolling_resistance_coefficient
        <= 0.05
    ):
        raise ValueError(
            "Rolling resistance coefficient must be between 0.001 and 0.05."
        )

    if not (
        0.50
        <= parameters.drivetrain_efficiency
        <= 1.0
    ):
        raise ValueError(
            "Drivetrain efficiency must be between 0.50 and 1.00."
        )

    if not (
        0.0
        <= parameters.regenerative_efficiency
        <= 1.0
    ):
        raise ValueError(
            "Regenerative efficiency must be between 0.00 and 1.00."
        )

    if not (
        0.0
        <= parameters.base_auxiliary_power_w
        <= 10000.0
    ):
        raise ValueError(
            "Base auxiliary power must be between 0 and 10000 W."
        )


def write_calibrated_yaml(
    source_yaml_path: str | Path,
    output_yaml_path: str | Path,
    parameters: CalibrationParameters,
) -> Path:
    validate_calibration_parameters(
        parameters
    )

    source = Path(source_yaml_path)
    output = Path(output_yaml_path)

    with source.open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = yaml.safe_load(handle) or {}

    vehicle = data.setdefault(
        "vehicle",
        {},
    )
    powertrain = data.setdefault(
        "powertrain",
        {},
    )
    auxiliary = data.setdefault(
        "auxiliary",
        {},
    )

    vehicle[
        "rolling_resistance_coefficient"
    ] = float(
        parameters.rolling_resistance_coefficient
    )

    powertrain[
        "drivetrain_efficiency"
    ] = float(
        parameters.drivetrain_efficiency
    )

    powertrain[
        "regenerative_efficiency"
    ] = float(
        parameters.regenerative_efficiency
    )

    auxiliary[
        "base_power_w"
    ] = float(
        parameters.base_auxiliary_power_w
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output.open(
        "w",
        encoding="utf-8",
    ) as handle:
        yaml.safe_dump(
            data,
            handle,
            sort_keys=False,
            allow_unicode=True,
        )

    return output


def backup_and_activate_yaml(
    active_yaml_path: str | Path,
    parameters: CalibrationParameters,
) -> tuple[Path, Path]:
    """
    Back up the current active YAML to:
        <config folder>/old/<stem>_YYYYMMDD_HHMMSS.yaml

    Then write the edited calibration values back to the active YAML path.
    """
    active = Path(
        active_yaml_path
    )

    if not active.is_file():
        raise FileNotFoundError(
            active
        )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    old_dir = (
        active.parent
        / "old"
    )
    old_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    backup = (
        old_dir
        / (
            f"{active.stem}_"
            f"{timestamp}"
            f"{active.suffix}"
        )
    )

    # Avoid an extremely unlikely same-second collision.
    counter = 1
    while backup.exists():
        backup = (
            old_dir
            / (
                f"{active.stem}_"
                f"{timestamp}_"
                f"{counter:02d}"
                f"{active.suffix}"
            )
        )
        counter += 1

    shutil.copy2(
        active,
        backup,
    )

    temporary = active.with_suffix(
        active.suffix + ".new"
    )

    try:
        write_calibrated_yaml(
            active,
            temporary,
            parameters,
        )
        temporary.replace(
            active
        )
    except Exception:
        temporary.unlink(
            missing_ok=True
        )
        raise

    return backup, active
