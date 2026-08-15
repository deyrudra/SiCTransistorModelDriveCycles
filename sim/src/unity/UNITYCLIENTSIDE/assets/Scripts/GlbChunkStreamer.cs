using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using GLTFast;
using UnityEngine;

public class GlbChunkStreamer : MonoBehaviour
{
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

        if (!File.Exists(path))
        {
            Debug.LogError($"[GLB] File does not exist: {path}");
            return;
        }

        Debug.Log($"[GLB] Loading {key}: {path}");

        GameObject chunkRoot = new GameObject(
            $"Chunk_{cx}_{cy}"
        );

        chunkRoot.transform.SetParent(
            transform,
            false
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
                Debug.LogError(
                    $"[GLB] Failed to parse chunk {key}"
                );

                Destroy(chunkRoot);
                return;
            }

            bool instantiated =
                await gltf.InstantiateMainSceneAsync(
                    chunkRoot.transform
                );

            if (!instantiated)
            {
                Debug.LogError(
                    $"[GLB] Failed to instantiate chunk {key}"
                );

                Destroy(chunkRoot);
                return;
            }

            loadedChunks[key] = chunkRoot;

            Debug.Log(
                $"[GLB] Loaded chunk {key}"
            );
        }
        catch (Exception exception)
        {
            Debug.LogError(
                $"[GLB] Error loading {key}: {exception}"
            );

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

        Debug.Log(
            $"[GLB] Unloaded chunk {key}"
        );
    }

    private string ChunkKey(
        int cx,
        int cy
    )
    {
        return $"{cx}_{cy}";
    }
}