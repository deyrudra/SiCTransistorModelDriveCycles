from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Optional

SRC = Path(__file__).resolve().parents[2] / "src"
if SRC.exists():
    sys.path.append(str(SRC))
    
from chunks.grid import world_to_chunk



from osm2world_converter import (
    convert_chunk,
    glb_chunk_path,
    glb_exists,
)


ChunkKey = tuple[int, int]


@dataclass(frozen=True)
class ReadyGlbChunk:
    cx: int
    cy: int
    path: Path

    @property
    def key(self) -> ChunkKey:
        return self.cx, self.cy


@dataclass
class ChunkUpdate:
    """
    Changes produced by one manager update.

    ready:
        Chunks that have become available and should be sent to Unity.

    unload:
        Chunks no longer required by the current streaming region.

    pending:
        Chunks currently being converted.

    failed:
        Mapping of chunks to their most recent conversion error.
    """

    ready: list[ReadyGlbChunk] = field(default_factory=list)
    unload: list[ChunkKey] = field(default_factory=list)
    pending: set[ChunkKey] = field(default_factory=set)
    failed: dict[ChunkKey, str] = field(default_factory=dict)


class GlbChunkManager:
    """
    Manage streamed GLB chunks around a simulation/world position.

    The manager does not communicate with Unity itself. It only manages GLB
    availability and reports which chunks became ready or should be unloaded.

    Typical use:

        manager = GlbChunkManager(radius=2)

        update = manager.update(camera_x, camera_y)

        for chunk in update.ready:
            send_chunk_load_to_unity(chunk)

        for cx, cy in update.unload:
            send_chunk_unload_to_unity(cx, cy)
    """

    def __init__(
        self,
        *,
        radius: int = 2,
        max_workers: int = 2,
        overwrite: bool = False,
    ) -> None:
        if radius < 0:
            raise ValueError("radius must be >= 0")

        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")

        self.radius = radius
        self.overwrite = overwrite

        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="glb-converter",
        )

        # Chunks Unity has already been told are available.
        self.loaded_chunks: set[ChunkKey] = set()

        # Conversion jobs currently running.
        self.pending: dict[ChunkKey, Future[Path]] = {}

        # Last failure for a chunk.
        self.failures: dict[ChunkKey, str] = {}

        # Most recently required streaming region.
        self.wanted_chunks: set[ChunkKey] = set()

        self.closed = False

    # ------------------------------------------------------------------
    # Chunk selection
    # ------------------------------------------------------------------

    def chunks_around(
        self,
        center_x: float,
        center_y: float,
        radius: Optional[int] = None,
    ) -> list[ChunkKey]:
        """
        Return chunks around a world-space location.

        Chunks are ordered from nearest to furthest so nearby geometry becomes
        available first.
        """

        r = self.radius if radius is None else max(0, int(radius))

        center_cx, center_cy = world_to_chunk(center_x, center_y)

        chunks = [
            (cx, cy)
            for cx in range(center_cx - r, center_cx + r + 1)
            for cy in range(center_cy - r, center_cy + r + 1)
        ]

        chunks.sort(
            key=lambda key:
            abs(key[0] - center_cx) + abs(key[1] - center_cy)
        )

        return chunks

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _convert_worker(self, key: ChunkKey) -> Path:
        cx, cy = key

        return convert_chunk(
            cx,
            cy,
            overwrite=self.overwrite,
        )

    def request_chunk(self, cx: int, cy: int) -> bool:
        """
        Ensure a chunk is either cached or being converted.

        Returns True when a new background conversion was started.
        """

        if self.closed:
            raise RuntimeError("GlbChunkManager is closed")

        key = (cx, cy)

        if glb_exists(cx, cy):
            return False

        if key in self.pending:
            return False

        future = self.executor.submit(
            self._convert_worker,
            key,
        )

        self.pending[key] = future
        self.failures.pop(key, None)

        return True

    # ------------------------------------------------------------------
    # Completed jobs
    # ------------------------------------------------------------------

    def _drain_completed(self) -> list[ReadyGlbChunk]:
        ready: list[ReadyGlbChunk] = []

        completed = [
            key
            for key, future in self.pending.items()
            if future.done()
        ]

        for key in completed:
            future = self.pending.pop(key)
            cx, cy = key

            try:
                path = future.result()

                if not path.is_file():
                    raise FileNotFoundError(
                        f"Conversion completed but GLB does not exist: {path}"
                    )

                if path.stat().st_size <= 0:
                    raise RuntimeError(
                        f"Conversion produced an empty GLB: {path}"
                    )

                self.failures.pop(key, None)

                # Only tell the caller about the chunk if it is still wanted.
                if key in self.wanted_chunks and key not in self.loaded_chunks:
                    self.loaded_chunks.add(key)

                    ready.append(
                        ReadyGlbChunk(
                            cx=cx,
                            cy=cy,
                            path=path,
                        )
                    )

            except Exception as exc:
                self.failures[key] = str(exc)

        return ready

    # ------------------------------------------------------------------
    # Streaming update
    # ------------------------------------------------------------------

    def update(
        self,
        center_x: float,
        center_y: float,
        *,
        radius: Optional[int] = None,
    ) -> ChunkUpdate:
        """
        Update the GLB streaming region around a simulation position.

        This method is intended to be called repeatedly from the main Python
        application loop.
        """

        if self.closed:
            raise RuntimeError("GlbChunkManager is closed")

        ordered_wanted = self.chunks_around(
            center_x,
            center_y,
            radius,
        )

        wanted = set(ordered_wanted)
        self.wanted_chunks = wanted

        # --------------------------------------------------------------
        # Unload chunks which have left the streaming area.
        # --------------------------------------------------------------

        unload = sorted(self.loaded_chunks - wanted)

        for key in unload:
            self.loaded_chunks.discard(key)

        # --------------------------------------------------------------
        # Collect conversions which finished since the previous update.
        # --------------------------------------------------------------

        ready = self._drain_completed()

        # --------------------------------------------------------------
        # Existing GLBs can become ready immediately.
        # --------------------------------------------------------------

        for cx, cy in ordered_wanted:
            key = (cx, cy)

            if key in self.loaded_chunks:
                continue

            if glb_exists(cx, cy):
                self.loaded_chunks.add(key)

                ready.append(
                    ReadyGlbChunk(
                        cx=cx,
                        cy=cy,
                        path=glb_chunk_path(cx, cy),
                    )
                )

                continue

            # Start conversion for missing GLB.
            self.request_chunk(cx, cy)

        return ChunkUpdate(
            ready=ready,
            unload=unload,
            pending=set(self.pending),
            failed=dict(self.failures),
        )

    # ------------------------------------------------------------------
    # Direct chunk access
    # ------------------------------------------------------------------

    def get_chunk(self, cx: int, cy: int) -> Optional[Path]:
        """
        Return a cached GLB path if the chunk is currently available.
        """

        if glb_exists(cx, cy):
            return glb_chunk_path(cx, cy)

        return None

    def retry_failed(self) -> None:
        """
        Allow failed chunks to be attempted again during future updates.
        """

        self.failures.clear()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self.closed:
            return

        self.closed = True

        self.executor.shutdown(
            wait=False,
            cancel_futures=True,
        )

        self.pending.clear()

    def __enter__(self) -> "GlbChunkManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ----------------------------------------------------------------------
# Simple standalone test
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import time

    manager = GlbChunkManager(
        radius=1,
        max_workers=2,
    )

    try:
        # Stuttgart project origin.
        x = 0.0
        y = 0.0

        while True:
            result = manager.update(x, y)

            for chunk in result.ready:
                print(
                    f"READY   ({chunk.cx:+d}, {chunk.cy:+d}) "
                    f"{chunk.path}"
                )

            for cx, cy in result.unload:
                print(
                    f"UNLOAD  ({cx:+d}, {cy:+d})"
                )

            if result.failed:
                for key, error in result.failed.items():
                    print(
                        f"FAILED  {key}: {error}"
                    )

            print(
                f"loaded={len(manager.loaded_chunks)} "
                f"pending={len(result.pending)}"
            )

            if not result.pending:
                break

            time.sleep(0.1)

    finally:
        manager.close()