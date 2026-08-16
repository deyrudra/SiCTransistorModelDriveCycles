# Single Route Summary Module

Before creating a multi-route comparator we can simply call that function, here is a single route summary module that runs the whole analysis and produces one compact result containing:

```
duration_s
distance_km
traction_energy_kwh
recovered_energy_kwh
net_energy_kwh
peak_dc_power_kw
peak_junction_temperature_c
relative_damage
```

The multi-route comparator can then call this single route summary module and compare via that.

Created two files `route_summary.py` and `run_route_summary.py`. 

Example Usage:

```bash
python -u .\sim\src\drive_cycles\run_route_summary.py `
  .\sim\cycles\drive_cycle_20260816_224216_veh101029.csv `
  --vehicle-config .\sim\src\drive_cycles\car_configs\tesla_model3_lr_rwd.yaml
```

