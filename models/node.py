class Node:
    def __init__(self, node_id, name, status, type, ram, flash, cpu, active_tasks, health_score, uptime, network):
        self.id = node_id
        self.name = name
        self.status = status
        self.type = type
        self.ram = ram
        self.flash = flash
        self.cpu = cpu
        self.active_tasks = active_tasks
        self.health_score = health_score
        self.uptime = uptime
        self.network = network

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "type": self.type,
            "ram": self.ram,
            "flash": self.flash,
            "cpu": self.cpu,
            "active_tasks": self.active_tasks,
            "health_score": self.health_score,
            "uptime": self.uptime,
            "network": self.network
        }
