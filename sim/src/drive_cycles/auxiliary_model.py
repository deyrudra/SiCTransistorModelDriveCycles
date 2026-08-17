from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuxiliaryLoad:
    base_power_w: float
    hvac_power_w: float
    hvac_enabled: bool

    @property
    def active_hvac_power_w(self) -> float:
        return self.hvac_power_w if self.hvac_enabled else 0.0

    @property
    def total_power_w(self) -> float:
        return self.base_power_w + self.active_hvac_power_w


def total_auxiliary_power_w(config) -> float:
    """
    Return the battery-side auxiliary electrical load.

    Auxiliary loads are deliberately kept separate from traction inverter
    dc_power_w so they do not create artificial SiC traction-inverter losses.
    """
    base = max(0.0, float(getattr(config, "base_auxiliary_power_w", 0.0)))
    hvac = max(0.0, float(getattr(config, "hvac_power_w", 0.0)))
    enabled = bool(getattr(config, "hvac_enabled", False))
    return base + (hvac if enabled else 0.0)
