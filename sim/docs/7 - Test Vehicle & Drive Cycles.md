# Test Vehicle & Drive Cycles

The test vehicle will serve as the current player, or in other words the car we are testing. The drive cycles will be recorded for this vehicle. All other traffic vehicles will remain the same and be part of a different class of vehicles.

A new class for the test vehicle will be created as `ego_vehicle.py`.

Additionally, `visualization.py` will be updated so that once A->B is selected, the ego vehicle is spawned on that route.

A `drive_cycle_recorder.py` will be used to record the raw drive cycles for the ego vehicle.



**Added vehicle config + elevation support.**

Introduced per-vehicle dynamics and asynchronous route elevation fetching for Stuttgart drive-cycle generation. Added `VehicleDynamicsConfig` loader (`sim/src/drive_cycles/vehicle_config.py`) and a Tesla Model 3 YAML (`car_configs/tesla_model3_lr_rwd.yaml`). Implemented `ElevationManager` (sim/src/drive_cycles/elevation_data.py) that queries `OpenTopoData`, persists a local cache, and provides segment grades.



**Vehicle Dynamics**

I now want the ego car to use the config file and also use the real road grade to calculate the actual acceleration for the car.

The physics model calculates:

```
F_rolling = Crr · m · g · cos(θ)


F_aero = 0.5 · ρ · Cd · A · v²


F_grade = m · g · sin(θ)


F_required = m · a_requested
           + F_rolling
           + F_aero
           + F_grade
```

To do this I've updated `ego_vehicle.py` and `visualization.py` and have added a new script called `vehicle_dynamics.py` which does the physics above.



**Validation (Quality-Control)**

- Two files were created `validate_drive_cycle.py`, `drive_cycle_loader.py`

- The `validate_drive_cycle.py` is the library, it reads one of the CSV files from the `sim/cycles/` folder, checks if its valid, and converts it to clean Python objects.

  - E.g.`python -m drive_cycles.validate_drive_cycle ..\cycles\drive_cycle_20260815_233000_veh100042.csv`

  - E.g. Internally:

    ```python
    from drive_cycles.drive_cycle_loader import (
        load_drive_cycle,
        build_mission_profile,
    )
    
    cycle = load_drive_cycle(
        "../cycles/drive_cycle_....csv"
    )
    
    profile = build_mission_profile(cycle)
    ```



