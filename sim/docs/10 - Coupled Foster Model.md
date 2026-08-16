# Coupled Foster Thermal Model

With the coupled Foster Thermal Model we can take the total semiconductor loss from the inverter electrical model and find the junction temperatures of the transistors.

Pipeline:

```
semiconductor loss
       ↓
representative device loss
       ↓
case / coolant thermal boundary
       ↓
4-pair Foster network
       ↓
junction temperature
       ↑
       └── Rds(on) changes with temperature
```

So for an output we will get:

```
time_s
dc_power_requested_w
phase_current_rms_a
device_current_rms_a

rds_on_device_ohm

aggregate_conduction_loss_w
aggregate_switching_loss_w
aggregate_total_loss_w

device_loss_w

case_temperature_c
junction_temperature_c

thermal_iterations
converged
```

Example Usage:

```bash
python -u .\sim\src\drive_cycles\run_thermal_profile.py `
  .\sim\cycles\drive_cycle_20260816_000650_veh100087.csv `
  --vehicle-config .\sim\src\drive_cycles\car_configs\tesla_model3_lr_rwd.yaml
```

There's no need to import the new `csv`'s form the previous scripts because this thermal profile script recalculates those internally from the raw drive cycle.

 
