# Rainflow Cycle Stage

This stage performs rainflow counting on the `junction_temperature_c`. This will extract thermal cycles such as:

```
ΔTj
mean Tj
cycle count
```

Current Pipeline:

```
route
  ↓
drive cycle
  ↓
longitudinal forces/power
  ↓
inverter current/loss
  ↓
coupled Foster thermal model
  ↓
junction_temperature_c
  ↓
ΔTj
mean Tj
cycle count
```

Example Usage:

```bash
python -u .\sim\src\drive_cycles\run_rainflow_analysis.py `
  .\sim\cycles\drive_cycle_20260816_000650_veh100087.csv `
  --vehicle-config .\sim\src\drive_cycles\car_configs\tesla_model3_lr_rwd.yaml
```



