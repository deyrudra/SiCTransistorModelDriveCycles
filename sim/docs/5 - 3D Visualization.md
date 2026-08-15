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