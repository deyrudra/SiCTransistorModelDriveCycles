class TrafficLight:
    def __init__(
        self,
        node_id,
        green_time=20.0,
        yellow_time=3.0,
        red_time=20.0
    ):
        self.node_id = node_id

        self.green_time = green_time
        self.yellow_time = yellow_time
        self.red_time = red_time

        self.state = "green"
        self.timer = 0.0


    def update(self, dt):
        self.timer += dt

        if self.state == "green":

            if self.timer >= self.green_time:
                self.state = "yellow"
                self.timer = 0.0

        elif self.state == "yellow":

            if self.timer >= self.yellow_time:
                self.state = "red"
                self.timer = 0.0

        elif self.state == "red":

            if self.timer >= self.red_time:
                self.state = "green"
                self.timer = 0.0