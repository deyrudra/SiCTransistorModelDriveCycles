import xml.etree.ElementTree as ET
import sys
from pathlib import Path
import math


SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.append(str(SRC))

from chunks.projection import latlon_to_local
from traffic.traffic_light import TrafficLight
from traffic.intersection import Intersection

DRIVABLE_HIGHWAYS = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
}

# Helper function to parse maxspeed values from OSM data
def parse_speed(maxspeed):
    if maxspeed is None:
        return None

    try:
        speed_kmh = float(maxspeed)
        return speed_kmh / 3.6

    except ValueError:
        return None

# Helper function to calculate the difference between two angles in radians
def angle_difference(a, b):
    diff = abs(a - b)

    while diff > math.pi:
        diff -= math.pi

    return abs(diff)
    

class RoadNode:
    def __init__(self, node_id, lat, lon):
        self.id = node_id
        self.lat = lat
        self.lon = lon

        self.x, self.y = latlon_to_local(lat, lon)

        self.traffic_light = False
        
class Road:
    def __init__(self, way_id):
        self.id = way_id

        self.nodes = []

        self.highway = None
        self.name = None
        self.maxspeed = None
        self.lanes = None
        self.oneway = False
        
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

        self.length = 0.0
        self.speed_limit = parse_speed(
            road.maxspeed
        )

class RoadNetwork:
    def __init__(self):
        self.nodes = {}
        self.roads = {}
        self.segments = {}
        self.traffic_lights = {}
        self.intersections = []

    def load_osm(self, filename):
        tree = ET.parse(filename)
        root = tree.getroot()

        self.load_nodes(root)
        self.load_roads(root)
        self.build_segments()
        
        self.calculate_segment_lengths()
        self.build_outgoing()
        
        self.build_traffic_lights()
        self.build_intersections()
        
    def build_network(self):
        self.build_segments()
        self.calculate_segment_lengths()
        self.build_outgoing()
        self.build_traffic_lights()
        self.build_intersections()
        
    def load_area(self, min_cx, max_cx, min_cy, max_cy):
        folder = Path(__file__).resolve().parents[2] / "cache" / "osm_chunks" / "stuttgart"

        for cx in range(min_cx, max_cx + 1):
            for cy in range(min_cy, max_cy + 1):

                filename = folder / f"chunk_{cx}_{cy}.osm"

                if not filename.exists():
                    print("Missing:", filename)
                    continue

                print("Loading:", filename)

                self.load_osm(filename)

        self.build_network()
            
    def load_nodes(self, root):
        for element in root.findall("node"):

            node_id = int(element.attrib["id"])
            lat = float(element.attrib["lat"])
            lon = float(element.attrib["lon"])

            node = RoadNode(
                node_id,
                lat,
                lon
            )

            tags = {}

            for tag in element.findall("tag"):
                tags[tag.attrib["k"]] = tag.attrib["v"]

            if tags.get("highway") == "traffic_signals":
                node.traffic_light = True

            self.nodes[node_id] = node

    def load_roads(self, root):

        for element in root.findall("way"):

            tags = {}

            for tag in element.findall("tag"):
                tags[tag.attrib["k"]] = tag.attrib["v"]

            if "highway" not in tags:
                continue
            
            highway = tags["highway"]

            if highway not in DRIVABLE_HIGHWAYS:
                continue

            way_id = int(element.attrib["id"])

            road = Road(way_id)

            road.highway = highway
            road.name = tags.get("name")
            road.maxspeed = tags.get("maxspeed")
            road.lanes = tags.get("lanes")

            road.oneway = tags.get("oneway") == "yes"

            for nd in element.findall("nd"):
                node_id = int(nd.attrib["ref"])

                road.nodes.append(node_id)

            self.roads[way_id] = road
            
    def build_segments(self):
        self.segments = []

        for road in self.roads.values():

            for i in range(len(road.nodes) - 1):

                u = road.nodes[i]
                v = road.nodes[i + 1]

                # forward segment
                self.segments.append(
                    RoadSegment(u, v, road)
                )

                # reverse segment for two-way roads
                if not road.oneway:
                    self.segments.append(
                        RoadSegment(v, u, road)
                    )
                    
    def build_outgoing(self):
        self.outgoing = {}

        for segment in self.segments:

            if segment.u not in self.outgoing:
                self.outgoing[segment.u] = []

            self.outgoing[segment.u].append(segment)
            
            
    def calculate_segment_lengths(self):
        for segment in self.segments:
            node_u = self.nodes[segment.u]
            node_v = self.nodes[segment.v]

            dx = node_v.x - node_u.x
            dy = node_v.y - node_u.y

            segment.length = math.hypot(dx, dy)
    
    def build_traffic_lights(self):

        self.traffic_lights = {}

        for node in self.nodes.values():

            if node.traffic_light:

                light = TrafficLight(
                    node_id=node.id
                )

                self.traffic_lights[node.id] = light
                
                
    def build_intersections(self):
        self.intersections = []

        traffic_nodes = [
            node
            for node in self.nodes.values()
            if node.traffic_light
        ]

        max_distance = 40.0
        used = set()

        for node in traffic_nodes:

            if node.id in used:
                continue

            intersection = Intersection(
                len(self.intersections)
            )

            intersection.signal_nodes.append(node.id)
            used.add(node.id)

            for other in traffic_nodes:

                if other.id in used:
                    continue

                dx = other.x - node.x
                dy = other.y - node.y

                distance = math.hypot(dx, dy)

                if distance <= max_distance:
                    intersection.signal_nodes.append(
                        other.id
                    )

                    used.add(other.id)

            # --------------------------------
            # Find valid signal approach angles
            # --------------------------------

            signal_angles = []

            for node_id in intersection.signal_nodes:

                incoming = self.incoming_segments(node_id)

                if not incoming:
                    continue

                segment = incoming[0]

                angle = self.segment_angle(segment)

                signal_angles.append(
                    (node_id, angle)
                )

            # --------------------------------
            # Split into two opposing phases
            # --------------------------------

            if signal_angles:

                reference_angle = signal_angles[0][1]

                for node_id, angle in signal_angles:

                    difference = angle_difference(
                        reference_angle,
                        angle
                    )

                    if difference < math.radians(45):
                        intersection.phase_a.append(node_id)

                    else:
                        intersection.phase_b.append(node_id)

            # Ignore intersections that do not control
            # any drivable approach
            if not intersection.phase_a and not intersection.phase_b:
                continue

            self.intersections.append(intersection)
                
            
    # Helper function to calculate the angle of a road segment
    def segment_angle(self, segment):
        node_u = self.nodes[segment.u]
        node_v = self.nodes[segment.v]

        dx = node_v.x - node_u.x
        dy = node_v.y - node_u.y

        angle = math.atan2(dy, dx)

        return angle
    

    def incoming_segments(self, node_id):
        incoming = []

        for segment in self.segments:

            if segment.v == node_id:
                incoming.append(segment)

        return incoming