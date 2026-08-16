# Lifetime / Damage Model: Relative SiC Damage Per Route



This stage converts each (Delta $T_j$, Mean $T_j$, Cycle Count) into relative SiC damage and sum the damage for the whole route.

Pipeline:

```
route
  ↓
drive cycle
  ↓
longitudinal power
  ↓
inverter losses
  ↓
junction temperature
  ↓
rainflow cycles
  ↓
NEXT: relative SiC damage
```

Created the `sim\src\drive_cycles\run_reliability_damage.py`
`sim\src\drive_cycles\reliability_damage.py` scripts.

Example Usage:

```bash
python -u .\sim\src\drive_cycles\run_reliability_damage.py `
  .\sim\cycles\drive_cycle_20260816_000650_veh100087.csv `
  --vehicle-config .\sim\src\drive_cycles\car_configs\tesla_model3_lr_rwd.yaml
```

