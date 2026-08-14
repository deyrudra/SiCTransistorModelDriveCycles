"""
Step 1
This file does two things their own question.
- world_to_chunk(x,y): returns the chunk that contains the world coordinates (x,y)
- chunk_bounds(cx,cy): returns the bounds of a chunk in world coordinates

"""

import math

CHUNK_SIZE = 250


def world_to_chunk(x, y):
    cx = math.floor(x / CHUNK_SIZE)
    cy = math.floor(y / CHUNK_SIZE)

    return cx, cy


def chunk_bounds(cx, cy):
    min_x = cx * CHUNK_SIZE
    min_y = cy * CHUNK_SIZE

    max_x = min_x + CHUNK_SIZE
    max_y = min_y + CHUNK_SIZE

    return min_x, min_y, max_x, max_y

# Test
if __name__ == "__main__":
    print(world_to_chunk(100, 100))
    print(world_to_chunk(300, 100))
    print(world_to_chunk(-10, 100))

    print(chunk_bounds(0, 0))
    print(chunk_bounds(1, 0))
    print(chunk_bounds(-1, 0))
