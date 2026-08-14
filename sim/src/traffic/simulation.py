from shapely import intersection


class Simulation:
    def __init__(self, network):
        self.network = network

        self.time = 0.0
        self.speed = 1.0

        self.vehicles = []


    def add_vehicle(self, vehicle):
        self.vehicles.append(vehicle)


    def update(self, real_dt):
        sim_dt = real_dt * self.speed

        self.time += sim_dt

        for intersection in self.network.intersections:
            intersection.update(sim_dt)

        for vehicle in self.vehicles:
            vehicle.update(sim_dt)