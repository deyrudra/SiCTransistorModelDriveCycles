# Test Vehicle

The test vehicle will serve as the current player, or in other words the car we are testing. The drive cycles will be recorded for this vehicle. All other traffic vehicles will remain the same and be part of a different class of vehicles.

A new class for the test vehicle will be created as `ego_vehicle.py`.

Additionally, `visualization.py` will be updated so that once A->B is selected, the ego vehicle is spawned on that route.

A `drive_cycle_recorder.py` will be used to record the raw drive cycles for the ego vehicle.