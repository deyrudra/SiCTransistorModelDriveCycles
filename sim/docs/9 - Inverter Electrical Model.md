# Inverter Electrical Model

As of now, we have the drive cycle -> power mission profile (dc_power, etc).

Now we want to take the dc power demand and convert it into inverter current to get the total semiconductor loss.

For every sample, this next stage should calculate:

```
dc_power_requested_w
dc_power_served_w
unserved_power_w

phase_current_rms_a
phase_current_peak_a

device_current_rms_a
device_current_peak_a

rds_on_device_ohm
conduction_loss_w
switching_loss_w
total_loss_w
```

So far the pipeline is:

```
dc_power_w
   ↓
DC bus voltage
   ↓
phase current
   ↓
parallel device sharing
   ↓
conduction loss
      +
switching loss
   ↓
total semiconductor loss
```

Example Usage:

```bash
python -u .\sim\src\drive_cycles\run_inverter_profile.py `
  .\sim\cycles\drive_cycle_20260816_000650_veh100087.csv `
  --vehicle-config .\sim\src\drive_cycles\car_configs\tesla_model3_lr_rwd.yaml
```

