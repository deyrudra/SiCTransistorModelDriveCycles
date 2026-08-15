# Vehicle Energy/Power Mission Profile (Longitudinal Profile)

Takes a validated drive cycle and calculates the following per sample:

```
time_s
v_mps
accel_mps2
grade_deg

force_inertial_n
force_rolling_n
force_aero_n
force_grade_n
force_total_n

wheel_power_w
dc_power_w
friction_brake_power_w
```

- Essentially it used to get: distance, traction energy, regenerated energy, peak wheel power, peak DC power

Example Usage: 

````bash
python -u "c:\Projects\SiCTransistorModelDriveCycles\sim\src\drive_cycles\run_longitudinal_profile.py" C:\Projects\SiCTransistorModelDriveCycles\sim\cycles\drive_cycle_20260816_000650_veh100087.csv  --vehicle-config .\sim\src\drive_cycles\car_configs\tesla_model3_lr_rwd.yaml
````

