class Intersection:
    def __init__(self, intersection_id):
        self.id = intersection_id
        
        self.signal_nodes = []

        self.phase_a = []
        self.phase_b = []

        self.state = "A_GREEN"
        self.timer = 0.0

        self.green_time = 20.0
        self.yellow_time = 3.0
        
    def get_signal_state(self, node_id):

        if node_id in self.phase_a:

            if self.state == "A_GREEN":
                return "green"

            if self.state == "A_YELLOW":
                return "yellow"

            return "red"


        if node_id in self.phase_b:

            if self.state == "B_GREEN":
                return "green"

            if self.state == "B_YELLOW":
                return "yellow"

            return "red"


        return None


    def update(self, dt):
        self.timer += dt

        if self.state == "A_GREEN":
            if self.timer >= self.green_time:
                self.state = "A_YELLOW"
                self.timer = 0.0

        elif self.state == "A_YELLOW":
            if self.timer >= self.yellow_time:
                self.state = "B_GREEN"
                self.timer = 0.0

        elif self.state == "B_GREEN":
            if self.timer >= self.green_time:
                self.state = "B_YELLOW"
                self.timer = 0.0

        elif self.state == "B_YELLOW":
            if self.timer >= self.yellow_time:
                self.state = "A_GREEN"
                self.timer = 0.0