from flask import Flask, render_template, jsonify, request
import json
import os
from datetime import datetime
from models.node import Node

app = Flask(__name__)
app.config.from_object('config.Config')

# Load system data
def load_system_data():
    try:
        with open('data/system_data.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        # Return default data if file doesn't exist
        return {
            "nodes": [
                {
                    "id": 1,
                    "name": "Node 1",
                    "status": "active",
                    "type": "STM32H743VIT6",
                    "ram": "1MB",
                    "flash": "2MB",
                    "cpu": "480MHz",
                    "active_tasks": ["Motor Control", "Sensor Fusion"],
                    "health_score": 85,
                    "uptime": "12h 34m",
                    "network": "CAN + Ethernet"
                },
                {
                    "id": 2,
                    "name": "Node 2",
                    "status": "standby",
                    "type": "STM32H743VIT6",
                    "ram": "1MB",
                    "flash": "2MB",
                    "cpu": "480MHz",
                    "active_tasks": ["Navigation"],
                    "health_score": 92,
                    "uptime": "1d 02h",
                    "network": "CAN + Ethernet"
                },
                {
                    "id": 3,
                    "name": "Node 3",
                    "status": "active",
                    "type": "STM32H743VIT6",
                    "ram": "1MB",
                    "flash": "2MB",
                    "cpu": "480MHz",
                    "active_tasks": ["Vision"],
                    "health_score": 78,
                    "uptime": "1d 05h",
                    "network": "CAN + Ethernet"
                }
            ],
            "failures": [
                {
                    "id": 1,
                    "timestamp": "2026-02-07 14:30:00",
                    "node_id": 1,
                    "description": "Node 1 reported as offline. Failover in progress.",
                    "status": "resolved"
                }
            ],
            "system_status": "active",
            "tasks": {
                "Motor Control": {"node_id": 1, "status": "running"},
                "Sensor Fusion": {"node_id": 1, "status": "running"},
                "Navigation": {"node_id": 2, "status": "running"},
                "Vision": {"node_id": 3, "status": "running"}
            }
        }

# Load system data once at startup
system_data = load_system_data()

@app.route('/')
def index():
    return render_template('index.html', system_data=system_data)

@app.route('/nodes')
def nodes():
    return render_template('nodes.html', system_data=system_data)

@app.route('/api/system_status')
def api_system_status():
    return jsonify({
        "status": system_data["system_status"],
        "nodes_online": len([n for n in system_data["nodes"] if n["status"] == "active"]),
        "tasks_running": len(system_data["tasks"]),
        "network_latency": 12
    })

@app.route('/api/nodes')
def api_nodes():
    return jsonify(system_data["nodes"])

@app.route('/api/failures')
def api_failures():
    return jsonify(system_data["failures"])

@app.route('/api/tasks')
def api_tasks():
    return jsonify(system_data["tasks"])

@app.route('/api/update_node', methods=['POST'])
def update_node():
    data = request.get_json()
    node_id = data.get('node_id')

    # Update node status in memory (in a real app, this would update the database)
    for node in system_data["nodes"]:
        if node["id"] == node_id:
            node["status"] = data.get('status', node["status"])
            break

    return jsonify({"success": True, "message": f"Node {node_id} updated successfully"})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5050)
