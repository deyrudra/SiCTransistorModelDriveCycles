"""
Step 5
This script inspects the downloaded OSM chunk and prints out some statistics about the number of nodes, ways, and relations, as well as specific features like traffic lights, stop signs, give way signs, buildings, and roads.
This is just to check if the .osm chunk actually contains the data we care about.



"""

from pathlib import Path
import xml.etree.ElementTree as ET


def inspect_chunk(cx, cy):
    filename = Path(__file__).resolve().parents[2] / "cache" / "osm_chunks" / "stuttgart" / f"chunk_{cx}_{cy}.osm"

    if not filename.exists():
        print("Chunk not found:", filename)
        return

    tree = ET.parse(filename)
    root = tree.getroot()

    nodes = root.findall("node")
    ways = root.findall("way")
    relations = root.findall("relation")

    print("Chunk:", cx, cy)
    print("Nodes:", len(nodes))
    print("Ways:", len(ways))
    print("Relations:", len(relations))

    traffic_lights = []
    stop_signs = []
    give_way = []
    buildings = []
    roads = []

    for node in nodes:
        tags = {
            tag.attrib["k"]: tag.attrib["v"]
            for tag in node.findall("tag")
        }

        highway = tags.get("highway")

        if highway == "traffic_signals":
            traffic_lights.append(node.attrib["id"])

        elif highway == "stop":
            stop_signs.append(node.attrib["id"])

        elif highway == "give_way":
            give_way.append(node.attrib["id"])

    for way in ways:
        tags = {
            tag.attrib["k"]: tag.attrib["v"]
            for tag in way.findall("tag")
        }

        if "highway" in tags:
            roads.append({
                "id": way.attrib["id"],
                "highway": tags.get("highway"),
                "name": tags.get("name"),
                "maxspeed": tags.get("maxspeed"),
                "lanes": tags.get("lanes"),
                "surface": tags.get("surface")
            })

        if "building" in tags:
            buildings.append(way.attrib["id"])

    print()
    print("Traffic lights:", len(traffic_lights))
    print("Stop signs:", len(stop_signs))
    print("Give way signs:", len(give_way))
    print("Buildings:", len(buildings))
    print("Roads:", len(roads))

    print()
    print("First few roads:")

    for road in roads[:10]:
        print(road)


if __name__ == "__main__":
    inspect_chunk(0, 0)