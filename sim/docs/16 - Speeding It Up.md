# Faster Execution without increasing simulation timestep

Now I am going to have faster execution of the EGO vehicle without increasing the simulation timestep.

- So a 10 minute simulated trip will contain 600 / 0.05 = 12000 physics steps, no matter how long it actually takes.

Plus multiple independent route simulations in parallel.

Difference of fast mode:

```
no drawing
no pygame frame cap
no camera work
no labels
no buildings
no HUD
no waiting for real time
```

How to use this?

- Once a route has been generated Route 1, Route 2, Route 3 in the GUI press F5 and the visualizer will preload elevation for all the routes (serially), and then in parallel launch independent worker processes for each route.

  - Each process will have it's own:

    ```
    RoadNetwork
    Simulation
    background vehicles
    traffic-light state
    EgoVehicle
    DriveCycleRecorder
    ```

- Results are still normal drive-cycle files

There are effectively two modes now: Visual Mode and Compute Mode

