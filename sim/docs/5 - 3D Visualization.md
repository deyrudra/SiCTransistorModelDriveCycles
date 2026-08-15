# 3D Visualization (Unity)

This documentation is for the Unity portion of the project. Where I will be creating a 3D visualization in unity by projecting the previous works into a 3D environment, and using the OSM2World tool to convert the `.osm` files to `.glb` files which will then be rendered.

Pipeline:

```
osm2world_converter.py
		↓
glb_chunk_manager.py
		↓
unity_protocol.py
		↓
unity_bridge.py
		↓
Unity PythonWorldClient.cs
		↓
Unity GlbChunkStreamer.cs
		↓
camera synchronization
		↓
vehicle synchronization
```

Prerequisites

```
```

**Step 1**

Creating `osm2world_converter.py`

- Input: `cache/osm_chunks/stuttgart/`
- Output: `cache/glb_chunks/stuttgart/`
- OSM2World Tool Location: `tools/osm2world/`
- Script Location: `src/unity/osm2world_converter.py`

This script is simple, it uses the command line tools in osm2world to convert the `.osm` file to `.glb` and stores it in the Output/Save Location.



**Step 2**
Creating `glb_chunk_manager.py`

- Functions:

  - decide which chunks are needed
  - reuse existing GLBs
  - request missing conversions
  - track pending / ready / failed chunks
  - report chunks to load/unload

- Purpose: Calling `GlbChunkManager(radius=2)` gets you a 5x5 chunk around the camera point. 

- ```
  250 m × 5 = 1250 m
  
         Unity streaming area
  ┌────┬────┬────┬────┬────┐
  │    │    │    │    │    │
  ├────┼────┼────┼────┼────┤
  │    │    │    │    │    │
  ├────┼────┼────┼────┼────┤
  │    │    │CAM │    │    │
  ├────┼────┼────┼────┼────┤
  │    │    │    │    │    │
  ├────┼────┼────┼────┼────┤
  │    │    │    │    │    │
  └────┴────┴────┴────┴────┘
  ```



**Step 3**
Creating `unity_protocol.py`

- Purpose: Define a clean message format between Python and Unity. This is not sending anything via sockets to unity, rather deciding what messages look like and provide encode/decode helpers.

- Message Types:

  ```
  hello
  chunk_load
  chunk_unload
  camera_state
  vehicles
  traffic_lights
  simulation_state
  ```

- Chunk Message Example:

  ```json
  {
    "type": "chunk_load",
    "cx": 0,
    "cy": 1,
    "path": "cache/glb_chunks/stuttgart/chunk_0_1.glb"
  }
  ```



**Step 4**
Creating `unity_bridge.py`

- Purpose: networking layer between your Python simulation and Unity
- Functions:
  - open a TCP server on Python
  - accept the Unity client
  - send NDJSON messages using `unity_protocol.py`
  - send `chunk_load` / `chunk_unload` events from `GlbChunkManager`
  - periodically send vehicles, traffic lights, and simulation state
  - receive camera/state messages from Unity
  - never block the simulation loop

**Step 5**
This step is loading the GLB models in Unity.

- Installing glTFast package in Unity:

  1. Open **Window → Package Manager**
  2. Click the `+`
  3. Choose **Install package by name...**
  4. Enter: `com.unity.cloud.gltfast`

- Also wrote two `cs` scripts:

  - `Assets/Assets/Scripts/GlbChunkStreamer.cs`

    `Assets/Assets/Scripts/PythonWorldClient.cs`

  - They can be found in the repo at: `SiCTransistorModelDriveCycles\sim\src\unity\UNITYCLIENTSIDE\assets\Scripts\`

This is really buggy, going to abandon for now.

