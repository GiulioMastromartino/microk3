# MicroK3 🚀

A Flask-based web dashboard for monitoring and managing distributed STM32H743VIT6 microcontroller nodes with automatic failover capabilities, now integrated with ROS 2.

![Version](https://img.shields.io/badge/version-0.2.0-blue)
![Python](https://img.shields.io/badge/python-3.9+-green)
![ROS 2](https://img.shields.io/badge/ROS%202-Humble%2Fjazzy-blueviolet)
![License](https://img.shields.io/badge/license-Apache%202.0-orange)

## Features

- 🤖 **ROS 2 Integration**: Bidirectional communication with ROS 2 / Micro-ROS agents
- 📊 **Real-time Monitoring**: Track node health, status, and performance
- 🔄 **Failover Management**: Automatic failure detection and logging
- 🎯 **Task Distribution**: Visualize task allocation across nodes
- 🔒 **Secure API**: Authentication and rate-limiting on write operations
- 📱 **Responsive Dashboard**: Modern web interface for system oversight
- 🔌 **RESTful API**: Programmatic access to system data

## Architecture

```
┌─────────────────────────────────────────────┐
│           Web Dashboard (Flask)             │
│  ┌────────────┐  ┌──────────────────────┐  │
│  │  Frontend  │  │    REST API          │  │
│  │  (HTML/JS) │  │  (Authenticated)     │  │
│  └─────┬──────┘  └──────────────────────┘  │
└────────┼────────────────────────────────────┘
         │
         ▼
┌──────────────────────┐      ┌───────────────────────────┐
│   ROS 2 Manager      │◄────►│  Micro-ROS Agent (Local)  │
│  (ros_interface.py)  │      └─────────────┬─────────────┘
└──────────────────────┘                    │
                                            ▼
                                ┌───────────────────────────┐
                                │   Distributed Nodes       │
                                │   (STM32H743VIT6)         │
                                └───────────────────────────┘
```

## ROS 2 Integration

This application runs a ROS 2 node (`microk3_dashboard`) that communicates on the following topics:

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

**System Alerts (`microk3/system_alerts`):**
```json
{
  "node_id": 1,
  "msg": "Overheating detected",
  "level": "warning"
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

- Python 3.9 or higher
- ROS 2 (Humble or Jazzy recommended) installed and sourced
- Micro-ROS Agent (if connecting to real hardware)

### Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/GiulioMastromartino/microk3.git
   cd microk3
   ```

2. **Setup Environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Source ROS 2 (Important!):**
   ```bash
   # Example for Ubuntu/Debian
   source /opt/ros/humble/setup.bash
   
   # Example for macOS (RoboStack)
   # source ~/miniforge3/activate
   ```

4. **Configuration:**
   ```bash
   cp .env.example .env
   # Generate secret key
   python -c "import secrets; print(f'SECRET_KEY={secrets.token_hex(32)}')" >> .env
   ```

5. **Run the Application:**
   ```bash
   python app.py
   ```
   *You should see "✅ ROS 2 Manager started" in the logs.*

## Development

### Running Without ROS 2
If ROS 2 libraries are not found, the application will automatically fall back to **Simulation Mode**. The dashboard will work, but ROS functionality will be disabled.

### Testing ROS 2 Connection
You can test the integration using CLI tools:

**Simulate a Node Update:**
```bash
ros2 topic pub --once /microk3/node_status std_msgs/msg/String "{data: '{\"id\": 1, \"status\": \"standby\", \"health\": 50}'}"
```

**Monitor Commands:**
```bash
ros2 topic echo /microk3/commands
```

## License

Apache 2.0
