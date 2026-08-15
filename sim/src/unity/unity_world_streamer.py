from __future__ import annotations

"""
Glue layer used by the 2D visualizer.

The Pygame visualizer owns one UnityWorldStreamer and calls update() with its
current camera position. The streamer:

    2D camera x/y
        -> GlbChunkManager
        -> chunk_load / chunk_unload protocol messages
        -> UnityBridge
        -> Unity

Do not run unity_bridge.py separately when using this integration.
"""

import time
from pathlib import Path
from typing import Optional

try:
    from .glb_chunk_manager import GlbChunkManager
    from .unity_bridge import UnityBridge
    from .unity_protocol import chunk_load_message, chunk_unload_message
except ImportError:
    from glb_chunk_manager import GlbChunkManager
    from unity_bridge import UnityBridge
    from unity_protocol import chunk_load_message, chunk_unload_message


class UnityWorldStreamer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        radius: int = 2,
        max_workers: int = 2,
        update_interval_s: float = 0.10,
    ) -> None:
        self.bridge = UnityBridge(host=host, port=port)
        self.chunks = GlbChunkManager(
            radius=radius,
            max_workers=max_workers,
        )

        self.update_interval_s = max(0.02, float(update_interval_s))
        self._next_update = 0.0
        self._last_center_chunk: Optional[tuple[int, int]] = None
        self._closed = False

    @property
    def connected(self) -> bool:
        return self.bridge.connected

    def start(self) -> None:
        self.bridge.start()
        print(
            f"[unity-stream] bridge started on "
            f"{self.bridge.host}:{self.bridge.port}"
        )

    def update(self, center_x: float, center_y: float) -> None:
        """
        Stream the GLB region surrounding the current 2D camera position.

        This is intentionally cheap enough to call once per Pygame frame.
        Actual manager work is throttled to update_interval_s.
        """
        if self._closed:
            return

        now = time.monotonic()
        if now < self._next_update:
            return
        self._next_update = now + self.update_interval_s

        result = self.chunks.update(center_x, center_y)

        for chunk in result.ready:
            self.bridge.send(
                chunk_load_message(
                    chunk.cx,
                    chunk.cy,
                    chunk.path,
                )
            )

        for cx, cy in result.unload:
            self.bridge.send(
                chunk_unload_message(cx, cy)
            )

        # Print failures only when useful. The manager can retry on later
        # updates after the OSM downloader has produced the source chunk.
        for key, error in result.failed.items():
            print(
                f"[unity-stream] GLB pending/failed {key}: {error}"
            )

    def close(self) -> None:
        if self._closed:
            return

        self._closed = True

        self.chunks.close()
        self.bridge.close()

        print("[unity-stream] closed")
