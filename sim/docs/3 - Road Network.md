# Road Network

Now using the download and caching system built for Chunk Generation, we can build a traffic system and road network. This simulation should be able performing the following functions:

- Copy the road network
- Identify and simulate the traffic light signals
- Run at any simulation speed
  - e.g. 0.5x, 1x, 10x, 100x speed.
- Add vehicles + traffic

Pipeline:

```
cached .osm chunks
       ↓
road network
       ↓
intersections + traffic lights
       ↓
simulation clock
       ↓
vehicles
       ↓
traffic simulation
```

Prerequisites:

```
pip install pygame pyproj shapely
```



**Step 1**

Building the road network in `sim/traffic/road_network.py`.

- This contains three classes:

  ```python
  class RoadNode:
      def __init__(self, node_id, lat, lon):
          self.id = node_id
          self.lat = lat
          self.lon = lon
  
  
  class Road:
      def __init__(self, way_id):
          self.id = way_id
          self.nodes = []
  
          self.highway = None
          self.name = None
          self.maxspeed = None
          self.lanes = None
          self.oneway = False
          
  class RoadNetwork:
      def __init__(self):
          self.nodes = {}
          self.roads = {}
  ```

  - The plan is for an object from the `RoadNetwork` class to contain the currently loaded road network.

In this file, in the `RoadNetwork` class, we also have the functions:

- `load_osm(self, filename)`: this loads one `.osm` chunk
- `load_nodes(self, root):` this loads the nodes in that chunk, and puts them into the road network.
  - `XML` to `RoadNode` Object: 
    - `<node id="9497087" lat="48.7762189" lon="9.1871323"/>`
    - `RoadNode(id=9497087, lat=48.7762189, lon=9.1871323)`
- `load_roads(self, root)`: this loads the roads in that chunk.



The test for the file is in `tests/traffic/test_road_network.py`



**Step 2**

Convert every OSM node from lat/lon into your local x/y meters

Right now, the `RoadNod` only has `id, lat, lon`, for simulation, routing, vehicle motion, and Unity later, we want `id, x, y`.

- To do this, we will use the same local coordinate system we are using for chunking.

So in the `road_network.py` file, we are updating it to reflect this.



**Step 3**

Filtering out non-drivable OSM ways. Right now the road network includes, footways, pedestrians, steps, etc. So in `road_network.py` we are filtering this out.

- This made the number of roads for the chunk go from 135 roads to 35 roads.
  - The test for this is in `test_road_network.py`



**Step 4**

Turn each drivable OSM way into individual road segments between consecutive roads. In  `road_network.py` we are adding a new class `RoadSegment`:

```python
class RoadSegment:
    def __init__(self, u, v, road):
        self.u = u
        self.v = v

        self.road_id = road.id
        self.highway = road.highway
        self.name = road.name
        self.maxspeed = road.maxspeed
        self.lanes = road.lanes
        self.oneway = road.oneway
```

We will also have the new method called `build_segments()`, which forms the road segments between two nodes.

- How?
  It goes through the roads which are formed like (A->B->C->D), and then forms segments with those nodes: (A,B), (B,C), (C,D).

More tests were added to the `test_road_network.py`.

**Step 5**

Making those road segments directional, so the network can actually support routing and traffic flow.

OSM way becomes: So now every OSM way is: `A->B, B->C`, and if its a two way road: `A->B, B->C, B->A, C->B.`

Testing now yields a higher number of segments which is a good sign, as there must be some two-way roads, and before it was only considering one-way segments.



**Step 6**

Now we need to add adjacency lookup. So this is basically, from node 199119 what are the next nodes that I could travel to?

I'm adding this to `RoadNetwork.__init__()`, and adding the function `build_outgoing(self)`, which goes through all the segments 



**Step 7**

Giving every `RoadSegment` a length in meters, to do this I implemented the `calcualte_segment_lengths()` function.

`test_road_network.py` was also updated to see if this function works.



**Step 8**

We turn the osm `maxspeed` into a numeric m/s value and store it per segment.

`test_road_network.py` was also updated.



**Step 9**

Detect traffic-light nodes from raw OSM data and attach that information into the road network. 

Because the OSM files are preserved, and they have raw node tags this makes it pretty easy. We will update `RoadNode` and the `load_nodes()` function to read the  node's tags.

`test_road_network.py` was also updated.



**Step 10**

Creating a Traffic-Light State Machine: `sim/src/traffic/traffic_light.py`

- This has an `update(self,dt)` function which takes in a parameter `dt` which means you advance the traffic light by 0.1 simulated seconds.

Created a new test file for this state machine: `traffic_light_test.py` in tests

Also connected it to the road network.

`test_road_network.py` was also updated.



**Step 11**

Building a simulation clock, so that traffic simulation can run at any speed.

To do this we need to separate real time from simulation time.

Creating a `simulation.py` file in `/traffic`, which will act as a multiplier of `real_dt` x `multipler_speed` which will then create the `sim_dt`

- E.g. 0.1 real seconds x 10 simulation speed = 1.0 simulated second

A test was created called `test_simulation.py`



**Step 12**

Right now traffic-signal nodes work independently, so we should ideally group nearby signal nodes into a single intersection and give them coordinates phases.

We will group traffic lights by distance, we will do this in a new class in `intersection.py`. The function `build_intersections(self)` is used to create the intersections, and group the traffic lights, using just distance, this function is actually a method in the `RoadNetwork` class.

We also added an `intersections` property to the `RoadNetwork` class. 



**Step 13**

Give each intersection two opposing phases: update to `intersection.py`

- Also created a helper function for later, where a vehicle can grab the intersections signal state via: `intersection.get_signal_state(signal_node_id)`

`test_road_network.py` was updated to show the two phases for intersections.



**Step 14**

The next step is to make `RoadNetwork` load multiple cached `.osm` chunks into one combined networks. 

Because 250m x 250m tiles are too small to reliably contain a whole intersection. So in our `load_osm()` file we want to add data and build the graph once after all chunks are loaded.

- updated `road_network.py` by adding a function called `build_network(self)`, which is called in the `load_area()` function which builds the network after loading in all the chunks.

`test_road_network.py` was updated to show the two phases for intersections.



VEHICLES + TRAFFIC

---

**Step 15**

Next step is to have vehicles moving along road segments. I did this in `vehicles.py`, and also created a subsequent test file.

- This includes, braking and acceleration.



**Step 16**





