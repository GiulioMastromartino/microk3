# MicroK3 🚀

A Flask-based web dashboard for monitoring and managing distributed STM32H743VIT6 microcontroller nodes with automatic failover capabilities, now integrated with ROS 2.

![Version](https://img.shields.io/badge/version-0.2.1-blue)
![Python](https://img.shields.io/badge/python-3.9+-green)
![ROS 2](https://img.shields.io/badge/ROS%202-Humble%2Fjazzy-blueviolet)
![License](https://img.shields.io/badge/license-Apache%202.0-orange)

## Features

- 🤖 **ROS 2 Integration**: Bidirectional communication with ROS 2 / Micro-ROS agents
- 🐳 **Docker-Native**: Runs the web dashboard and Micro-ROS agent in orchestrated containers
- 📊 **Real-time Monitoring**: Track node health, status, and performance
- 🔄 **Failover Management**: Automatic failure detection and logging
- 🎯 **Task Distribution**: Visualize task allocation across nodes
- 🔒 **Secure API**: Authentication and rate-limiting on write operations
- 📱 **Responsive Dashboard**: Modern web interface for system oversight
- 🔌 **RESTful API**: Programmatic access to system data

## Architecture

```
┌─────────────────────────────────────────────┐
│             Docker Environment              │
│                                             │
│  ┌──────────────────┐    ┌───────────────┐  │
│  │   Web Dashboard  │    │Micro-ROS Agent│  │
│  │   (Flask App)    ◄────►   (Humble)    │  │
│  └──────────────────┘    └───────┬───────┘  │
└──────────────────────────────────┼──────────┘
                                   │ UDP:8888
                                   ▼
                       ┌───────────────────────────┐
                       │   Distributed Nodes       │
                       │   (STM32H743VIT6)         │
                       └───────────────────────────┘
```

## ROS 2 Integration

The application uses a dedicated ROS 2 node (`microk3_dashboard`) running inside the Docker container to communicate with the Micro-ROS Agent.

| Topic | Type | Direction | Description |
|-------|------|-----------|-------------|
| `microk3/node_status` | `std_msgs/String` | Subscriber | Updates node status/health (JSON) |
| `microk3/system_alerts` | `std_msgs/String` | Subscriber | Logs system failures (JSON) |
| `microk3/commands` | `std_msgs/String` | Publisher | Sends commands to nodes (JSON) |

### JSON Formats

**Node Status (`microk3/node_status`):**
```json
{
  "id": 1,
  "status": "active",
  "health": 95,
  "uptime": "12h 34m"
}
```

**Commands (`microk3/commands`):**
```json
{
  "target_id": 1,
  "command": "SET_STATUS:standby"
}
```

## Installation

### Prerequisites

- Docker and Docker Compose
- (Optional) Git

### Quick Start (Recommended)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/GiulioMastromartino/microk3.git
   cd microk3
   ```

2. **Configuration:**
   ```bash
   cp .env.example .env
   # Generate secret key
   python3 -c "import secrets; print(f'SECRET_KEY={secrets.token_hex(32)}')" >> .env
   ```

3. **Run with Docker Compose:**
   This starts both the Dashboard and the Micro-ROS Agent.
   ```bash
   docker-compose up --build
   ```

4. **Connect your Hardware:**
   Configure your STM32 Micro-ROS client to connect to your computer's IP address on **UDP Port 8888**.

5. **Access Dashboard:**
   Open http://localhost:5050

## Simulating Nodes

If you don't have hardware yet, you can simulate nodes using the CLI or a Python script inside the container.

### Option 1: One-Liner (CLI)
Run this command from your host terminal to inject a simulated node:

```bash
# 1. Enter the running container
docker exec -it microk3 bash

# 2. Source ROS 2 (Required)
source /opt/ros/humble/setup.bash

# 3. Publish a fake node status
ros2 topic pub --once /microk3/node_status std_msgs/msg/String "{data: '{\"id\": 1, \"status\": \"active\", \"health\": 100, \"uptime\": \"0h 1m\", \"type\": \"Virtual Node\"}'}"
```

### Option 2: Python Simulation Script
Create a file named `simulate_nodes.py` (or run interactively inside the container):

```python
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import json
import time
import random

def main():
    rclpy.init()
    node = Node('virtual_stm32')
    publisher = node.create_publisher(String, '/microk3/node_status', 10)
    
    print("🚀 Starting Virtual Node Simulation...")
    
    try:
        while True:
            # Simulate Node 1
            msg = String()
            data = {
                "id": 1,
                "status": "active",
                "health": random.randint(90, 100),
                "uptime": "1h 30m",
                "type": "Simulated STM32"
            }
            msg.data = json.dumps(data)
            publisher.publish(msg)
            print(f"Published: {msg.data}")
            
            time.sleep(2.0) # Update every 2 seconds
            
    except KeyboardInterrupt:
        print("Stopping simulation...")
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
```

## Development

### Running Without Docker (MacOS/Linux)
If you prefer running natively, you must have ROS 2 installed on your host machine.

1. **Setup Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Source ROS 2 (Important!):**
   ```bash
   source /opt/ros/humble/setup.bash
   ```

3. **Run:**
   ```bash
   python app.py
   ```

## License

Apache 2.0
