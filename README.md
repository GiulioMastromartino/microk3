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

### Testing ROS 2 Connection
You can test the integration by sending messages from your host machine (if ROS 2 is installed locally) or by executing into the container:

**Simulate a Node Update:**
```bash
# Execute into the running microk3 container
docker exec -it microk3 bash

# Send a test message
ros2 topic pub --once /microk3/node_status std_msgs/msg/String "{data: '{\"id\": 1, \"status\": \"standby\", \"health\": 50}'}"
```

## License

Apache 2.0
