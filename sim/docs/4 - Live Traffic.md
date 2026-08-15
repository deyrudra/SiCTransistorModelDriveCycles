# Live Traffic Integration

Using TomTom's Traffic Flow API, we will retrieve current traffic conditions around the visible Stuttgart simulation area and use them to influence the simulated traffic.

TomTom does not provide the exact positions or number of real vehicles. Instead, the Traffic Flow API provides information such as:

- Current road speed
- Free-flow road speed
- Traffic confidence
- Road closure information

We can compare the current traffic speed against the normal free-flow speed to estimate how congested the road currently is.

Pipeline:

```text
TomTom Traffic Flow API
current road conditions
        |
        ▼
GPS Sample Points
lat/lon around visible simulation area
        |
        ▼
Current Speed / Free Flow Speed
traffic congestion ratio
        |
        ▼
Live Traffic State
speed factor + density factor
        |
        ▼
Traffic Simulation
vehicle speed + vehicle amount
```

Prerequisites:

```bash
pip install requests
```

A TomTom developer API key is also required.

The API key should be stored as an environment variable rather than directly inside the Python source code.

For Windows PowerShell:

```powershell
$env:TOMTOM_API_KEY="YOUR_API_KEY"
```

For Linux/macOS:

```bash
export TOMTOM_API_KEY="YOUR_API_KEY"
```

**Step 1**

Create the `live_traffic_tomtom.py` file.

This file is responsible for communicating with the TomTom Traffic Flow API and converting the returned traffic information into values that can be used by our simulation.

The TomTom API key is loaded from the environment:

```python
TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY")
```

This avoids storing the API key directly inside the project source code.

**Step 2**

Define how frequently the simulation should request new live traffic information.

```python
REFRESH_INTERVAL_S = 300.0
```

300 seconds is equal to 5 minutes.

Traffic conditions do not need to be downloaded every simulation frame because real traffic changes much more slowly than the simulation rendering.

Refreshing every 5 minutes also prevents unnecessary TomTom API requests.

**Step 3**

Create a function that queries TomTom traffic information for one GPS coordinate.

The function takes:

```text
latitude
longitude
API key
```

and queries the TomTom Flow Segment Data API for the road closest to that location.

The useful returned values are:

```text
currentSpeed
freeFlowSpeed
confidence
roadClosure
```

`currentSpeed` is the current estimated average speed of traffic on the road.

`freeFlowSpeed` represents the expected road speed when traffic is flowing normally.

`confidence` represents TomTom's confidence in the returned traffic information.

**Step 4**

Calculate the traffic congestion ratio.

For each successful TomTom response:

```text
congestion ratio = currentSpeed / freeFlowSpeed
```

Example:

```text
currentSpeed  = 30 km/h
freeFlowSpeed = 50 km/h

ratio = 30 / 50
ratio = 0.60
```

A value close to:

```text
1.0
```

means traffic is travelling close to its normal free-flow speed.

A lower value means the road is experiencing more congestion.

For example:

```text
1.00 = free flowing
0.80 = slight slowdown
0.60 = moderate congestion
0.40 = heavy congestion
0.20 = very heavy congestion
```

This ratio does not directly tell us how many vehicles are physically on the road.

It is used as an estimate of current traffic conditions.

**Step 5**

Sample multiple points around the visible simulation area.

Querying one point would only tell us about one nearby road.

Instead, several points are generated around the current camera position.

Example:

```text
             sample
                ●

      ●         ●         ●

                ●
```

The center point represents the current camera position while the surrounding points sample other parts of the visible map.

The local simulation coordinates are converted back into GPS coordinates using:

```python
local_to_latlon(x, y)
```

from `projection.py`.

This lets the simulation use its local meter coordinate system while TomTom receives latitude and longitude coordinates.

**Step 6**

Query TomTom for each sample point.

Each generated GPS sample is sent to the Traffic Flow API.

For every successful response we calculate:

```python
currentSpeed / freeFlowSpeed
```

Failed requests are ignored rather than stopping the simulation.

A request may fail because of:

```text
No internet connection
Invalid API key
TomTom API limit
No nearby supported road
Temporary API failure
```

The traffic simulation should continue running even when live traffic information cannot be retrieved.

**Step 7**

Combine the traffic samples.

Because TomTom provides a confidence value, the different samples can be combined using a confidence-weighted average.

Conceptually:

```text
Sample 1
ratio = 0.80
confidence = 0.95

Sample 2
ratio = 0.55
confidence = 0.90

Sample 3
ratio = 0.70
confidence = 0.75

        |
        ▼

Weighted Average Traffic Ratio
```

Higher-confidence TomTom measurements therefore have more influence on the final traffic value.

The result represents an estimate of traffic conditions around the currently visible simulation area.

**Step 8**

Run TomTom API requests outside the main Pygame simulation thread.

HTTP requests can take hundreds of milliseconds or several seconds depending on the network connection.

Running them directly inside the Pygame update loop would cause the simulation to temporarily freeze.

Therefore the TomTom traffic requests are performed asynchronously.

Pipeline:

```text
Pygame Main Thread
        |
        ├──────────────► Continue simulation
        |
        ▼
Background TomTom Request
        |
        ▼
Traffic result becomes available
        |
        ▼
Main simulation receives new traffic state
```

The simulation therefore continues rendering while TomTom data is being retrieved.

**Step 9**

Convert the returned congestion ratio into a simulation speed factor.

The traffic ratio can influence how quickly simulated vehicles travel.

For example:

```text
TomTom ratio = 0.65
```

can produce approximately:

```text
traffic_speed_factor = 0.65
```

A vehicle whose normal target speed is:

```text
50 km/h
```

could therefore receive an adjusted target speed of approximately:

```text
50 × 0.65 = 32.5 km/h
```

The existing road speed limit still defines the normal vehicle target speed.

TomTom modifies that value according to the current real-world traffic conditions.

**Step 10**

Apply the live traffic factor inside `vehicle.py`.

Previously the vehicle target speed was based mainly on:

```text
OSM road speed limit
traffic lights
vehicle ahead
```

The live traffic integration adds another condition:

```text
OSM speed limit
        |
        ▼
TomTom traffic factor
        |
        ▼
Adjusted target speed
        |
        ▼
Traffic light / vehicle checks
        |
        ▼
Vehicle acceleration or braking
```

This means vehicles still obey the existing simulation rules, but their expected road speed now changes with current traffic conditions.

**Step 11**

Use TomTom traffic conditions to influence simulated traffic density.

TomTom does not provide an exact vehicle count.

Therefore we should not interpret the API response as:

```text
"There are exactly 57 vehicles here."
```

Instead, the congestion information can be used as a heuristic for how many simulated vehicles should exist around the visible area.

For example:

```text
Free flowing traffic
        |
        ▼
Lower simulated vehicle target

Moderate congestion
        |
        ▼
Medium simulated vehicle target

Heavy congestion
        |
        ▼
Higher simulated vehicle target
```

The number of vehicles should change gradually rather than instantly spawning or deleting a large number of cars.

**Step 12**

Integrate the live traffic manager into `visualization.py`.

The visualizer already contains the main simulation loop.

The live traffic system is updated from this loop, but the HTTP request itself remains asynchronous.

The visualizer performs approximately:

```text
Update camera
        |
        ▼
Load OSM chunks
        |
        ▼
Check TomTom refresh timer
        |
        ▼
Receive new traffic result if available
        |
        ▼
Update live speed/density factors
        |
        ▼
Update simulation
        |
        ▼
Render map + vehicles
```

This keeps the TomTom integration separate from the existing OSM chunk downloader.

**Step 13**

Keep OpenStreetMap and TomTom responsible for different information.

OpenStreetMap remains responsible for the physical road network:

```text
Road geometry
Road connections
Street names
Speed limits
Traffic signal locations
Road classifications
```

TomTom is responsible for temporary live traffic conditions:

```text
Current traffic speed
Free-flow traffic speed
Congestion
Road closures
Traffic confidence
```

The complete data pipeline becomes:

```text
OpenStreetMap
        |
        ▼
Road Geometry
        |
        ├──────────────────────┐
        │                      │
        ▼                      ▼
Traffic Simulation       TomTom Traffic API
                               |
                               ▼
                         Live Traffic State
                               |
        ┌──────────────────────┘
        ▼
Vehicle Behaviour
        |
        ▼
Stuttgart Traffic Simulation
```

**Step 14**

If TomTom is unavailable, the simulation falls back to its normal behaviour.

The live traffic system should not be required for the basic simulator to function.

If no TomTom data is available:

```text
TomTom unavailable
        |
        ▼
No live traffic update
        |
        ▼
Use normal OSM speed limits
        |
        ▼
Simulation continues
```

This allows the project to still work:

```text
offline
without an API key
when the TomTom API fails
when the API request limit is reached
```

**Step 15**

Current limitation: traffic conditions are estimated for the visible area rather than every individual road.

At the moment, multiple TomTom points are sampled around the camera and combined into a general live traffic value.

This means nearby roads receive approximately the same overall traffic influence.

A future improvement will be to associate TomTom Flow Segment responses directly with individual OSM roads.

Future pipeline:

```text
OSM Road Segment
        |
        ▼
Road midpoint GPS coordinate
        |
        ▼
TomTom Flow Segment
        |
        ▼
Road-specific live speed
        |
        ▼
Individual RoadSegment traffic factor
```

This would allow different roads in Stuttgart to have different traffic conditions at the same time.

For example:

```text
Road A
50 km/h free flow
48 km/h current
        |
        ▼
Almost no congestion

Road B
50 km/h free flow
18 km/h current
        |
        ▼
Heavy congestion
```

Vehicles travelling on Road A could therefore continue normally while vehicles travelling on Road B encounter realistic live congestion.