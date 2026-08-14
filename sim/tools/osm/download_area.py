from download_chunk import get_chunk


def download_area(min_cx, max_cx, min_cy, max_cy):
    for cx in range(min_cx, max_cx + 1):
        for cy in range(min_cy, max_cy + 1):
            print()
            print("Chunk:", cx, cy)

            try:
                get_chunk(cx, cy)
            except Exception as e:
                print("Failed:", cx, cy, e)


if __name__ == "__main__":
    download_area(
        min_cx=-2,
        max_cx=2,
        min_cy=-2,
        max_cy=2
    )