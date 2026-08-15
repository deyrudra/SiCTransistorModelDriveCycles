using System;
using System.Collections.Generic;
using System.IO;
using GLTFast;
using UnityEngine;

public class GlbChunkStreamer : MonoBehaviour
{
    [Header("Chunk Grid")]
    [Tooltip("Must match CHUNK_SIZE in Python chunks/grid.py.")]
    public float chunkSizeMeters = 250f;

    [Tooltip(
        "OSM2World exports each independently converted chunk around its own local origin. " +
        "When enabled, Unity places that local origin at the center of the matching 250 m chunk."
    )]
    public bool chunkModelsAreCentered = true;

    [Header("Coordinate Mapping")]
    [Tooltip("Python simulation Y is mapped to Unity Z.")]
    public bool invertUnityZ = false;

    private readonly Dictionary<string, GameObject> loadedChunks =
        new Dictionary<string, GameObject>();

    public async void LoadChunk(int cx, int cy, string path)
    {
        string key = ChunkKey(cx, cy);

        if (loadedChunks.ContainsKey(key))
        {
            Debug.Log($"[GLB] Chunk already loaded: {key}");
            return;
        }

        path = Path.GetFullPath(path);

        if (!File.Exists(path))
        {
            Debug.LogError($"[GLB] File does not exist: {path}");
            return;
        }

        Debug.Log($"[GLB] Loading {key}: {path}");

        GameObject chunkRoot = new GameObject($"Chunk_{cx}_{cy}");
        chunkRoot.transform.SetParent(transform, false);

        // Every .glb is generated independently by OSM2World, so its model
        // coordinates are local to that export. Place that local export at the
        // corresponding Python 250 m grid location.
        chunkRoot.transform.localPosition = ChunkWorldPosition(cx, cy);

        Debug.Log(
            $"[GLB] Position {key} -> Unity " +
            $"{chunkRoot.transform.localPosition}"
        );

        try
        {
            byte[] data = await File.ReadAllBytesAsync(path);

            GltfImport gltf = new GltfImport();

            bool loaded = await gltf.LoadGltfBinary(
                data,
                new Uri(path)
            );

            if (!loaded)
            {
                Debug.LogError($"[GLB] Failed to parse chunk {key}");
                Destroy(chunkRoot);
                return;
            }

            bool instantiated =
                await gltf.InstantiateMainSceneAsync(
                    chunkRoot.transform
                );

            if (!instantiated)
            {
                Debug.LogError($"[GLB] Failed to instantiate chunk {key}");
                Destroy(chunkRoot);
                return;
            }

            loadedChunks[key] = chunkRoot;

            Debug.Log(
                $"[GLB] Loaded chunk {key} at " +
                $"{chunkRoot.transform.localPosition}"
            );
        }
        catch (Exception exception)
        {
            Debug.LogError($"[GLB] Error loading {key}: {exception}");
            Destroy(chunkRoot);
        }
    }

    public void UnloadChunk(int cx, int cy)
    {
        string key = ChunkKey(cx, cy);

        if (!loadedChunks.TryGetValue(
                key,
                out GameObject chunk
            ))
        {
            return;
        }

        loadedChunks.Remove(key);
        Destroy(chunk);

        Debug.Log($"[GLB] Unloaded chunk {key}");
    }

    private Vector3 ChunkWorldPosition(int cx, int cy)
    {
        float x;
        float z;

        if (chunkModelsAreCentered)
        {
            // Python chunk bounds:
            //
            // min_x = cx * 250
            // max_x = min_x + 250
            //
            // An independently exported OSM2World model is normally centered
            // around its own data, so put its origin at the chunk center.
            x = (cx + 0.5f) * chunkSizeMeters;
            z = (cy + 0.5f) * chunkSizeMeters;
        }
        else
        {
            // Use this mode if your generated GLBs have their local origin at
            // the south-west/min corner instead of the center.
            x = cx * chunkSizeMeters;
            z = cy * chunkSizeMeters;
        }

        if (invertUnityZ)
        {
            z = -z;
        }

        // Python X -> Unity X
        // Python Y -> Unity Z
        // Unity Y remains height.
        return new Vector3(x, 0f, z);
    }

    private string ChunkKey(int cx, int cy)
    {
        return $"{cx}_{cy}";
    }
}
