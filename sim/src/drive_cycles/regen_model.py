from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegenSplit:
    speed_mps: float
    braking_wheel_power_w: float
    fade_factor: float
    regen_wheel_power_w: float
    recovered_dc_power_w: float
    friction_brake_power_w: float


def regen_fade_factor(
    speed_mps: float,
    *,
    cutoff_speed_mps: float,
    full_regen_speed_mps: float,
) -> float:
    """
    Linear low-speed regen fade.

    0 below cutoff_speed_mps,
    1 at/above full_regen_speed_mps,
    linear between them.
    """
    v = max(0.0, float(speed_mps))
    cutoff = max(0.0, float(cutoff_speed_mps))
    full = max(cutoff + 1e-9, float(full_regen_speed_mps))

    if v <= cutoff:
        return 0.0
    if v >= full:
        return 1.0

    return (v - cutoff) / (full - cutoff)


def split_regen_and_friction(
    *,
    speed_mps: float,
    braking_wheel_power_w: float,
    regenerative_efficiency: float,
    max_regen_dc_power_w: float,
    cutoff_speed_mps: float,
    full_regen_speed_mps: float,
) -> RegenSplit:
    """
    Split requested negative wheel power into regenerative and friction braking.

    braking_wheel_power_w is a positive magnitude.

    The maximum regenerative wheel-side power is derived from the configured
    maximum DC recovery power and regenerative efficiency, then multiplied by
    the low-speed fade factor. Any remaining braking demand is assigned to the
    friction brakes.
    """
    braking = max(0.0, float(braking_wheel_power_w))
    eta = min(1.0, max(0.0, float(regenerative_efficiency)))
    max_dc = max(0.0, float(max_regen_dc_power_w))

    fade = regen_fade_factor(
        speed_mps,
        cutoff_speed_mps=cutoff_speed_mps,
        full_regen_speed_mps=full_regen_speed_mps,
    )

    if eta <= 0.0 or braking <= 0.0 or fade <= 0.0:
        regen_wheel = 0.0
        recovered_dc = 0.0
    else:
        max_regen_wheel = (max_dc / eta) * fade
        regen_wheel = min(braking, max_regen_wheel)
        recovered_dc = regen_wheel * eta

    friction = max(0.0, braking - regen_wheel)

    return RegenSplit(
        speed_mps=max(0.0, float(speed_mps)),
        braking_wheel_power_w=braking,
        fade_factor=fade,
        regen_wheel_power_w=regen_wheel,
        recovered_dc_power_w=recovered_dc,
        friction_brake_power_w=friction,
    )
