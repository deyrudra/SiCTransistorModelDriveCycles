class Vehicle:
    def __init__(self, vehicle_id, segment, network, simulation):
        self.id = vehicle_id
        self.segment = segment
        self.network = network
        self.simulation = simulation

        self.position = 0.0
        self.speed = 0.0

        self.acceleration = 2.0
        self.braking = 4.0

        self.safe_distance = 5.0
        
    def get_target_speed(self):
        if self.segment.speed_limit is None:
            target_speed = 10.0
        else:
            target_speed = self.segment.speed_limit

        light_state = self.get_traffic_light_state()

        if light_state in ("red", "yellow"):
            distance_to_light = (
                self.segment.length - self.position
            )

            stop_distance = self.stopping_distance()

            if distance_to_light <= stop_distance + 5.0:
                return 0.0
            
            
        distance_ahead = self.distance_to_vehicle_ahead()

        if distance_ahead is not None:

            if distance_ahead <= self.safe_distance:
                return 0.0

        return target_speed
        
    
    def update_speed(self, dt):
        target_speed = self.get_target_speed()

        if self.speed < target_speed:
            self.speed += self.acceleration * dt

            if self.speed > target_speed:
                self.speed = target_speed

        elif self.speed > target_speed:
            self.speed -= self.braking * dt

            if self.speed < target_speed:
                self.speed = target_speed

    def update(self, dt):
        self.update_speed(dt)

        self.position += self.speed * dt

        while self.position >= self.segment.length:

            light_state = self.get_traffic_light_state()

            if light_state in ("red", "yellow"):
                self.position = self.segment.length - 0.1
                self.speed = 0.0
                return

            self.position -= self.segment.length

            previous_node = self.segment.u
            next_node = self.segment.v

            outgoing = self.network.outgoing.get(
                next_node,
                []
            )

            choices = [
                segment
                for segment in outgoing
                if segment.v != previous_node
            ]

            if not choices:
                self.speed = 0.0
                self.position = self.segment.length
                return

            self.segment = choices[0]

    def get_position(self):
        start = self.network.nodes[self.segment.u]
        end = self.network.nodes[self.segment.v]

        if self.segment.length == 0:
            return start.x, start.y

        t = self.position / self.segment.length

        x = start.x + (end.x - start.x) * t
        y = start.y + (end.y - start.y) * t

        return x, y
    
    def get_traffic_light_state(self):
        node_id = self.segment.v

        for intersection in self.network.intersections:
            state = intersection.get_signal_state(node_id)

            if state is not None:
                return state

        return None
    
    def stopping_distance(self):
        if self.braking <= 0:
            return 0.0

        return (self.speed * self.speed) / (2 * self.braking)
    
    
    def distance_to_vehicle_ahead(self):
        nearest = None

        for other in self.simulation.vehicles:

            if other is self:
                continue

            if other.segment is not self.segment:
                continue

            if other.position <= self.position:
                continue

            distance = other.position - self.position

            if nearest is None or distance < nearest:
                nearest = distance

        return nearest