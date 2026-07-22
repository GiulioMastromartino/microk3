from flask import Flask, render_template, jsonify, request, abort
from flask_httpauth import HTTPBasicAuth
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import json
import os
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from functools import wraps
import threading
import sys
import traceback

from models.node import Node
from metrics import (
    DockerStatsCollector,
    build_metrics_summary,
    merge_container_metrics,
    merge_performance_metrics,
    new_metrics_state,
)
from config import get_config

# Initialize Flask app
app = Flask(__name__, template_folder='templates')
app.config.from_object(get_config())

# Setup logging first to capture startup errors
log_file_path = Path(app.config['LOG_FILE'])
log_file_path.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, app.config['LOG_LEVEL']),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Attempt to import ROS interface, handle failure if ROS 2 not installed
try:
    from ros_interface import ROS2Manager
    ROS_AVAILABLE = True
    logger.info("ROS 2 libraries loaded successfully")
except ImportError as e:
    ROS_AVAILABLE = False
    logger.warning(f"⚠️  ROS 2 libraries not found: {e}")
    print(f"DEBUG: ROS Import Error: {e}")
except Exception as e:
    ROS_AVAILABLE = False
    logger.error(f"⚠️  Unexpected error loading ROS 2 interface: {e}")
    traceback.print_exc()

# Load environment variables
load_dotenv()

# Initialize authentication
auth = HTTPBasicAuth()

# Store admin credentials (in production, use database with hashed passwords)
users = {
    app.config['ADMIN_USERNAME']: generate_password_hash(app.config['ADMIN_PASSWORD'])
}

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=app.config['RATELIMIT_DEFAULT'].split(';'),
    storage_uri=app.config['RATELIMIT_STORAGE_URL']
)

# ROS 2 Manager Instance
ros_manager = None
docker_stats_collector = None
ROS_WATCH_LIMIT = 10
ROS_SAMPLE_HISTORY_LIMIT = 20
ros_state_lock = threading.RLock()

@auth.verify_password
def verify_password(username: str, password: str) -> Optional[str]:
    """Verify user credentials"""
    if username in users and check_password_hash(users.get(username), password):
        return username
    return None


def get_empty_data() -> Dict[str, Any]:
    """Return empty system data structure (for clean state)"""
    return {
        "nodes": [],
        "failures": [],
        "system_status": "waiting_for_nodes",
        "tasks": {},
        "metrics": new_metrics_state(),
    }


def get_default_data() -> Dict[str, Any]:
    """Return startup data without simulated nodes."""
    logger.info("Initializing dashboard with empty live state")
    return get_empty_data()


def get_empty_ros_state() -> Dict[str, Any]:
    """Return empty ROS inspector state."""
    return {
        "graph_nodes": [],
        "graph_topics": [],
        "watched_topics": {},
        "last_graph_refresh": None,
        "graph_error": None,
    }


def refresh_system_status(data: Dict[str, Any]) -> None:
    """Recompute aggregate status from the currently detected nodes."""
    nodes = data.get("nodes", [])
    if not nodes:
        data["system_status"] = "waiting_for_nodes"
        return

    offline_states = {"offline", "error", "failed"}
    active_states = {"active", "online"}

    if any(getattr(node, "status", "unknown") in offline_states for node in nodes):
        data["system_status"] = "degraded"
    elif any(getattr(node, "status", "unknown") in active_states for node in nodes):
        data["system_status"] = "active"
    else:
        data["system_status"] = "monitoring"


def load_system_data() -> Dict[str, Any]:
    """Load system data, but never restore cached nodes or demo state."""
    try:
        data_file = Path(app.config['DATA_FILE'])
        if data_file.exists():
            logger.info(f"Ignoring persisted node snapshot from {data_file}; waiting for live ROS discovery")
            return get_empty_data()

        logger.warning(f"Data file not found: {data_file}. Initializing empty state.")
        return get_default_data()
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in data file: {e}")
        return get_default_data()
    except Exception as e:
        logger.error(f"Error loading system data: {e}")
        return get_default_data()


def save_system_data(data: Dict[str, Any]) -> bool:
    """Save system data to JSON file"""
    try:
        data_file = Path(app.config['DATA_FILE'])
        data_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert Node objects to dictionaries
        save_data = data.copy()
        if 'nodes' in save_data:
            save_data['nodes'] = [
                node.to_dict() if isinstance(node, Node) else node 
                for node in save_data['nodes']
            ]
        
        # Write to temporary file first, then rename (atomic operation)
        temp_file = data_file.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            json.dump(save_data, f, indent=4)
        
        temp_file.replace(data_file)
        logger.info(f"Saved system data to {data_file}")
        return True
        
    except Exception as e:
        logger.error(f"Error saving system data: {e}")
        return False


def read_log_lines(limit: int = 100) -> List[str]:
    """Read the latest application log lines."""
    log_file = Path(app.config['LOG_FILE'])
    if not log_file.exists():
        return []

    with open(log_file, 'r') as f:
        log_lines = f.readlines()[-limit:]

    log_lines.reverse()
    return [line.rstrip('\n') for line in log_lines]


def get_node_log_lines(node_id: int, limit: int = 100) -> List[str]:
    """Return log lines relevant to a specific node."""
    node_patterns = [
        re.compile(rf"\bNode {node_id}\b"),
        re.compile(rf"\bnode_id['\"]?:\s*{node_id}\b"),
        re.compile(rf"\bnode {node_id}\b", re.IGNORECASE),
        re.compile(rf"\bRAW_HEARTBEAT\b.*\bnode_id={node_id}\b"),
    ]

    matched_lines = []
    for line in read_log_lines(limit=500):
        if any(pattern.search(line) for pattern in node_patterns):
            matched_lines.append(line)
        if len(matched_lines) >= limit:
            break

    return matched_lines


# Global variable to hold system data
system_data = None  # Will be initialized in main block
ros_inspector_state = get_empty_ros_state()


def require_json(f):
    """Decorator to ensure request has JSON content"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return jsonify({"error": "Content-Type must be application/json"}), 400
        return f(*args, **kwargs)
    return wrapper


def get_node_by_id(node_id: int) -> Optional[Node]:
    """Find node by ID"""
    if not system_data or 'nodes' not in system_data:
        return None
    for node in system_data['nodes']:
        if node.id == node_id:
            return node
    return None


def serialize_system_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert runtime system data into template/API-safe dictionaries."""
    template_data = data.copy()
    template_data["nodes"] = [node.to_dict() for node in data.get("nodes", [])]
    template_data["metrics"] = build_metrics_summary(data.get("metrics", new_metrics_state()))
    return template_data


def get_metrics_summary() -> Dict[str, Any]:
    return build_metrics_summary(system_data.setdefault("metrics", new_metrics_state()))


def apply_runtime_metrics_to_nodes() -> None:
    def format_metric(metric_ms, sync_ready: bool, sync_scoped: bool = False) -> str:
        if sync_scoped and not sync_ready:
            return "syncing"
        if metric_ms is None:
            return "unknown"
        return f"{float(metric_ms):.3f} ms"

    metrics = get_metrics_summary()
    aggregate = metrics.get("aggregate", {})
    node_metrics = metrics.get("nodes", {})
    for node in system_data.get("nodes", []):
        per_node = node_metrics.get(str(node.id), {})
        node_aggregate = per_node.get("aggregate", aggregate)
        node.cpu = f"{float(aggregate.get('cpu_percent', 0.0)):.1f}% stack"
        node.ram = f"{int(aggregate.get('memory_usage_bytes', 0)) / (1024 * 1024):.1f} MiB stack"
        sync_ready = node_aggregate.get("sync_ready", False)
        node.network = (
            f"C->A {format_metric(node_aggregate.get('client_to_agent_ms'), sync_ready, sync_scoped=True)} | "
            f"A->ROS {format_metric(node_aggregate.get('agent_to_ros_ms'), sync_ready)} | "
            f"E2E {format_metric(node_aggregate.get('end_to_end_ms'), sync_ready, sync_scoped=True)} | "
            f"BW {float(node_aggregate.get('bandwidth_bps', 0.0)) / 1024.0:.1f} KiB/s"
        )


def get_ros_graph_payload() -> Dict[str, Any]:
    """Return a JSON-safe snapshot of the ROS inspector state."""
    with ros_state_lock:
        topics = [dict(topic) for topic in ros_inspector_state["graph_topics"]]
        watched_topics = {
            name: {
                "topic_name": topic_data["topic_name"],
                "topic_type": topic_data.get("topic_type"),
                "latest": topic_data.get("latest"),
                "history": list(topic_data.get("history", [])),
                "last_update": topic_data.get("last_update"),
                "status": topic_data.get("status", "idle"),
                "error": topic_data.get("error"),
            }
            for name, topic_data in ros_inspector_state["watched_topics"].items()
        }

        return {
            "ros_connected": bool(ROS_AVAILABLE and ros_manager and ros_manager.running),
            "nodes": list(ros_inspector_state["graph_nodes"]),
            "topics": topics,
            "watched_topics": watched_topics,
            "last_graph_refresh": ros_inspector_state.get("last_graph_refresh"),
            "graph_error": ros_inspector_state.get("graph_error"),
        }


def _upsert_watched_topic(topic_name: str, topic_type: Optional[str] = None) -> Dict[str, Any]:
    watched = ros_inspector_state["watched_topics"].setdefault(
        topic_name,
        {
            "topic_name": topic_name,
            "topic_type": topic_type,
            "latest": None,
            "history": [],
            "last_update": None,
            "status": "idle",
            "error": None,
        },
    )
    if topic_type:
        watched["topic_type"] = topic_type
    return watched


def append_failure(node_id: Optional[int], description: str, status: str = "warning") -> None:
    """Append a failure entry, avoiding exact duplicate consecutive records."""
    failures = system_data.setdefault("failures", [])
    if failures:
        last_failure = failures[-1]
        if (
            last_failure.get("node_id") == node_id
            and last_failure.get("description") == description
            and last_failure.get("status") == status
        ):
            return

    failures.append(
        {
            "id": len(failures) + 1,
            "timestamp": datetime.now().isoformat(),
            "node_id": node_id,
            "description": description,
            "status": status,
        }
    )

# ----------------------------------------------------------------------------
# ROS 2 Integration Logic
# ----------------------------------------------------------------------------

def ros_update_callback(action, data):
    """Callback function called by ROS thread to update Flask state"""
    with app.app_context():
        try:
            if action == 'update_node':
                node_id = data.get('id')
                # Check if node exists, if not create it (auto-discovery)
                node = get_node_by_id(node_id)
                new_status = data.get('status')
                
                if node:
                    previous_status = getattr(node, "status", "unknown")
                    # Update existing node
                    if new_status is not None:
                        node.status = new_status
                    if 'health' in data:
                        node.health_score = data['health']
                    if 'uptime' in data:
                        node.uptime = data['uptime']

                    if new_status == "offline" and previous_status != "offline":
                        append_failure(node_id, "Node transitioned offline", "warning")
                        logger.warning("ROS transition: Node %s offline", node_id)
                    elif previous_status == "offline" and new_status not in (None, "offline"):
                        logger.info("ROS transition: Node %s recovered to %s", node_id, new_status)
                    logger.info(f"ROS update: Node {node_id} updated")
                else:
                    # Auto-discover new node
                    logger.info(f"ROS discovery: New Node {node_id} detected")
                    new_node = Node(
                        id=node_id,
                        name=f"Node {node_id}",
                        status=new_status or 'unknown',
                        type="STM32H743VIT6", # Default, can be updated if msg includes it
                        ram="Unknown",
                        flash="Unknown",
                        cpu="Unknown",
                        active_tasks=[],
                        health_score=data.get('health', 100),
                        uptime=data.get('uptime', '0s'),
                        network="ROS 2"
                    )
                    system_data['nodes'].append(new_node)

                refresh_system_status(system_data)
                save_system_data(system_data)

            elif action == 'add_failure':
                node_id = data.get('node_id')
                # Log failure even if node is unknown yet
                append_failure(
                    node_id,
                    data.get('msg', 'Unknown Error'),
                    data.get('level', 'warning')
                )
                refresh_system_status(system_data)
                save_system_data(system_data)
                logger.warning(f"ROS alert: {data}")

            elif action == 'raw_heartbeat':
                node_id = data.get('id')
                heartbeat_payload = data.get('heartbeat_raw')
                if node_id is not None and heartbeat_payload is not None:
                    logger.info(
                        "RAW_HEARTBEAT node_id=%s payload=%s",
                        node_id,
                        json.dumps(heartbeat_payload, sort_keys=True),
                    )

            elif action == 'performance_metrics':
                merge_performance_metrics(system_data.setdefault("metrics", new_metrics_state()), data)
                apply_runtime_metrics_to_nodes()

            elif action == 'container_metrics':
                merge_container_metrics(system_data.setdefault("metrics", new_metrics_state()), data)
                apply_runtime_metrics_to_nodes()

            elif action == 'graph_snapshot':
                with ros_state_lock:
                    ros_inspector_state["graph_nodes"] = data.get("nodes", [])
                    ros_inspector_state["graph_topics"] = data.get("topics", [])
                    ros_inspector_state["last_graph_refresh"] = data.get("timestamp")
                    ros_inspector_state["graph_error"] = None

                    watched_names = set(ros_inspector_state["watched_topics"].keys())
                    available_topics = {
                        topic["name"]: topic for topic in ros_inspector_state["graph_topics"]
                    }
                    for topic_name in watched_names:
                        watched = ros_inspector_state["watched_topics"][topic_name]
                        if topic_name not in available_topics and watched["status"] != "error":
                            watched["status"] = "stale"
                        elif topic_name in available_topics and watched["status"] == "stale":
                            watched["status"] = "active"

            elif action == 'graph_snapshot_error':
                with ros_state_lock:
                    ros_inspector_state["graph_error"] = data.get("error")
                    ros_inspector_state["last_graph_refresh"] = data.get("timestamp")

            elif action == 'watch_started':
                with ros_state_lock:
                    watched = _upsert_watched_topic(
                        data["topic_name"],
                        data.get("topic_type"),
                    )
                    watched["status"] = "waiting"
                    watched["error"] = None

            elif action == 'watch_stopped':
                with ros_state_lock:
                    ros_inspector_state["watched_topics"].pop(data["topic_name"], None)

            elif action == 'watch_error':
                with ros_state_lock:
                    watched = _upsert_watched_topic(
                        data["topic_name"],
                        data.get("topic_type"),
                    )
                    watched["status"] = "error"
                    watched["error"] = data.get("error")

            elif action == 'topic_sample':
                with ros_state_lock:
                    watched = _upsert_watched_topic(
                        data["topic_name"],
                        data.get("topic_type"),
                    )
                    sample = {
                        "timestamp": data.get("timestamp"),
                        "value": data.get("sample"),
                    }
                    watched["latest"] = sample
                    watched["history"].insert(0, sample)
                    watched["history"] = watched["history"][:ROS_SAMPLE_HISTORY_LIMIT]
                    watched["last_update"] = data.get("timestamp")
                    watched["status"] = "active"
                    watched["error"] = None

        except Exception as e:
            logger.error(f"Error in ROS callback: {e}")


system_data = load_system_data()

# ============================================================================
# WEB ROUTES
# ============================================================================

@app.route('/')
def index():
    """Main dashboard page"""
    if not system_data:
        return "System initializing...", 503

    template_data = serialize_system_data(system_data)
    
    ros_connected = False
    if ROS_AVAILABLE and ros_manager:
        ros_connected = ros_manager.running

    return render_template(
        'index.html',
        system_data=template_data,
        ros_connected=ros_connected,
        metrics_summary=get_metrics_summary(),
    )


@app.route('/nodes')
def nodes():
    """Nodes detail page"""
    if not system_data:
        return "System initializing...", 503
        
    template_data = serialize_system_data(system_data)
    return render_template('node.html', system_data=template_data)


@app.route('/ros')
def ros_page():
    """ROS 2 graph and live topic viewer page."""
    if not system_data:
        return "System initializing...", 503

    return render_template('ros.html', ros_state=get_ros_graph_payload())


@app.route('/failures')
def failures():
    """Failures history page"""
    if not system_data:
        return "System initializing...", 503
        
    template_data = system_data.copy()
    # Sort failures by timestamp descending
    template_data['failures'] = sorted(
        system_data.get('failures', []), 
        key=lambda x: x['timestamp'], 
        reverse=True
    )
    return render_template('failures.html', system_data=template_data)


@app.route('/network')
def network():
    """Network status page"""
    if not system_data:
        return "System initializing...", 503
    
    ros_connected = False
    if ROS_AVAILABLE and ros_manager:
        ros_connected = ros_manager.running
        
    return render_template('network.html', system_data=system_data, ros_connected=ros_connected)


@app.route('/configuration')
@auth.login_required
def configuration():
    """Configuration page"""
    config_data = {
        "admin_username": app.config.get('ADMIN_USERNAME'),
        "log_level": app.config.get('LOG_LEVEL'),
        "ros_domain_id": os.environ.get('ROS_DOMAIN_ID', '0'),
        "flask_env": os.environ.get('FLASK_ENV', 'production')
    }
    return render_template('configuration.html', config=config_data)


@app.route('/logs')
@auth.login_required
def logs():
    """View application logs"""
    try:
        log_lines = read_log_lines()
    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        log_lines = [f"Error reading logs: {e}"]

    return render_template('logs.html', logs=log_lines, node=None)


@app.route('/nodes/<int:node_id>/logs')
def node_logs(node_id: int):
    """View logs related to a specific node."""
    if not system_data:
        return "System initializing...", 503

    node = get_node_by_id(node_id)
    if not node:
        abort(404)

    try:
        log_lines = get_node_log_lines(node_id)
    except Exception as e:
        logger.error(f"Error reading logs for node {node_id}: {e}")
        log_lines = [f"Error reading logs: {e}"]

    return render_template('logs.html', logs=log_lines, node=node)


# ============================================================================
# API ROUTES (Read-only, no auth required)
# ============================================================================

@app.route('/api/system_status')
@limiter.limit("30 per minute")
def api_system_status():
    """Get system status summary"""
    try:
        if not system_data:
            return jsonify({"error": "System initializing"}), 503

        active_nodes = sum(1 for n in system_data['nodes'] if n.is_active)
        
        ros_connected = False
        if ROS_AVAILABLE and ros_manager:
            ros_connected = ros_manager.running

        return jsonify({
            "status": system_data.get("system_status", "unknown"),
            "nodes_online": active_nodes,
            "total_nodes": len(system_data['nodes']),
            "tasks_running": len(system_data.get('tasks', {})),
            "client_to_agent_ms": (
                get_metrics_summary()["aggregate"].get("client_to_agent_ms")
                if get_metrics_summary()["aggregate"].get("sync_ready", False)
                else None
            ),
            "client_to_agent_jitter_ms": get_metrics_summary()["aggregate"].get("client_to_agent_jitter_ms", 0.0),
            "agent_to_ros_ms": get_metrics_summary()["aggregate"].get("agent_to_ros_ms"),
            "agent_to_ros_jitter_ms": get_metrics_summary()["aggregate"].get("agent_to_ros_jitter_ms", 0.0),
            "end_to_end_ms": (
                get_metrics_summary()["aggregate"].get("end_to_end_ms")
                if get_metrics_summary()["aggregate"].get("sync_ready", False)
                else None
            ),
            "end_to_end_jitter_ms": get_metrics_summary()["aggregate"].get("end_to_end_jitter_ms", 0.0),
            "network_latency": (
                get_metrics_summary()["aggregate"].get("lag_ms", 0.0)
                if get_metrics_summary()["aggregate"].get("sync_ready", False)
                else None
            ),
            "jitter_ms": get_metrics_summary()["aggregate"].get("jitter_ms", 0.0),
            "bandwidth_bps": get_metrics_summary()["aggregate"].get("bandwidth_bps", 0.0),
            "sync_ready": get_metrics_summary()["aggregate"].get("sync_ready", False),
            "clock_offset_ms": get_metrics_summary()["aggregate"].get("clock_offset_ms"),
            "clock_scale": get_metrics_summary()["aggregate"].get("clock_scale"),
            "time_sync_rtt_ms": get_metrics_summary()["aggregate"].get("time_sync_rtt_ms"),
            "time_sync_rtt_jitter_ms": get_metrics_summary()["aggregate"].get("time_sync_rtt_jitter_ms", 0.0),
            "time_sync_samples": get_metrics_summary()["aggregate"].get("time_sync_samples", 0),
            "cpu_percent": get_metrics_summary()["aggregate"].get("cpu_percent", 0.0),
            "memory_percent": get_metrics_summary()["aggregate"].get("memory_percent", 0.0),
            "timestamp": datetime.now().isoformat(),
            "ros_connected": ros_connected
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting system status: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/nodes')
@limiter.limit("30 per minute")
def api_nodes():
    """Get all nodes"""
    try:
        nodes_data = [node.to_dict() for node in system_data['nodes']]
        return jsonify(nodes_data), 200
    except Exception as e:
        logger.error(f"Error getting nodes: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/nodes/<int:node_id>')
@limiter.limit("30 per minute")
def api_node_detail(node_id: int):
    """Get specific node details"""
    try:
        node = get_node_by_id(node_id)
        if not node:
            return jsonify({"error": f"Node {node_id} not found"}), 404
        
        return jsonify(node.to_dict()), 200
        
    except Exception as e:
        logger.error(f"Error getting node {node_id}: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/failures')
@limiter.limit("30 per minute")
def api_failures():
    """Get failure history"""
    try:
        return jsonify(system_data.get('failures', [])), 200
    except Exception as e:
        logger.error(f"Error getting failures: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/nodes/<int:node_id>/logs')
@limiter.limit("30 per minute")
def api_node_logs(node_id: int):
    """Get filtered application logs for a specific node."""
    try:
        node = get_node_by_id(node_id)
        if not node:
            return jsonify({"error": f"Node {node_id} not found"}), 404

        return jsonify({
            "node_id": node_id,
            "node_name": node.name,
            "lines": get_node_log_lines(node_id),
        }), 200
    except Exception as e:
        logger.error(f"Error getting logs for node {node_id}: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/tasks')
@limiter.limit("30 per minute")
def api_tasks():
    """Get task status"""
    try:
        return jsonify(system_data.get('tasks', {})), 200
    except Exception as e:
        logger.error(f"Error getting tasks: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/metrics/summary')
@limiter.limit("120 per minute")
def api_metrics_summary():
    try:
        return jsonify(get_metrics_summary()), 200
    except Exception as e:
        logger.error(f"Error getting metrics summary: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/metrics/topics')
@limiter.limit("120 per minute")
def api_metrics_topics():
    try:
        return jsonify(get_metrics_summary().get("topics", {})), 200
    except Exception as e:
        logger.error(f"Error getting topic metrics: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/metrics/containers')
@limiter.limit("120 per minute")
def api_metrics_containers():
    try:
        return jsonify({
            "timestamp": get_metrics_summary().get("container_last_update"),
            "aggregate": get_metrics_summary().get("aggregate", {}),
            "containers": get_metrics_summary().get("containers", {}),
        }), 200
    except Exception as e:
        logger.error(f"Error getting container metrics: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/metrics/nodes/<int:node_id>')
@limiter.limit("120 per minute")
def api_metrics_node(node_id: int):
    try:
        node_metrics = get_metrics_summary().get("nodes", {}).get(str(node_id))
        if node_metrics is None:
            return jsonify({"error": f"Metrics for node {node_id} not found"}), 404
        return jsonify(node_metrics), 200
    except Exception as e:
        logger.error(f"Error getting node metrics for {node_id}: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/ros/graph')
@limiter.limit("120 per minute")
def api_ros_graph():
    """Get ROS graph state, topics, nodes, and watched topics."""
    try:
        return jsonify(get_ros_graph_payload()), 200
    except Exception as e:
        logger.error(f"Error getting ROS graph: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/ros/topics/<path:topic_name>/samples')
@limiter.limit("240 per minute")
def api_ros_topic_samples(topic_name: str):
    """Get latest sample and history for one watched topic."""
    try:
        normalized_name = "/" + topic_name.lstrip("/")
        with ros_state_lock:
            watched = ros_inspector_state["watched_topics"].get(normalized_name)
            if watched is None:
                return jsonify({"error": f"Topic {normalized_name} is not being watched"}), 404

            payload = {
                "topic_name": watched["topic_name"],
                "topic_type": watched.get("topic_type"),
                "latest": watched.get("latest"),
                "history": list(watched.get("history", [])),
                "last_update": watched.get("last_update"),
                "status": watched.get("status", "idle"),
                "error": watched.get("error"),
            }
        return jsonify(payload), 200
    except Exception as e:
        logger.error(f"Error getting ROS topic samples for {topic_name}: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/ros/watch', methods=['POST'])
@require_json
@limiter.limit("60 per minute")
def api_ros_watch_topic():
    """Start watching a ROS topic by name."""
    try:
        data = request.get_json() or {}
        topic_name = str(data.get("topic_name", "")).strip()
        if not topic_name:
            return jsonify({"error": "Missing required field: topic_name"}), 400

        normalized_name = "/" + topic_name.lstrip("/")
        with ros_state_lock:
            if normalized_name not in ros_inspector_state["watched_topics"] and len(ros_inspector_state["watched_topics"]) >= ROS_WATCH_LIMIT:
                return jsonify({"error": f"Watch limit reached ({ROS_WATCH_LIMIT})"}), 400

        if not (ROS_AVAILABLE and ros_manager and ros_manager.running):
            return jsonify({"error": "ROS 2 is not connected"}), 503

        result = ros_manager.watch_topic(normalized_name)
        status = 200 if result.get("success") else 400
        return jsonify(result), status
    except Exception as e:
        logger.error(f"Error watching ROS topic: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/ros/unwatch', methods=['POST'])
@require_json
@limiter.limit("60 per minute")
def api_ros_unwatch_topic():
    """Stop watching a ROS topic by name."""
    try:
        data = request.get_json() or {}
        topic_name = str(data.get("topic_name", "")).strip()
        if not topic_name:
            return jsonify({"error": "Missing required field: topic_name"}), 400

        normalized_name = "/" + topic_name.lstrip("/")
        if not (ROS_AVAILABLE and ros_manager and ros_manager.running):
            with ros_state_lock:
                if normalized_name in ros_inspector_state["watched_topics"]:
                    ros_inspector_state["watched_topics"].pop(normalized_name, None)
                    return jsonify({"success": True, "topic_name": normalized_name}), 200
            return jsonify({"error": "ROS 2 is not connected"}), 503

        result = ros_manager.unwatch_topic(normalized_name)
        status = 200 if result.get("success") else 404
        return jsonify(result), status
    except Exception as e:
        logger.error(f"Error unwatching ROS topic: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ============================================================================
# API ROUTES (Write operations, require authentication)
# ============================================================================

@app.route('/api/update_node', methods=['POST'])
@auth.login_required
@require_json
@limiter.limit("10 per minute")
def update_node():
    """Update node status (requires authentication)"""
    try:
        data = request.get_json()
        
        if 'node_id' not in data:
            return jsonify({"error": "Missing required field: node_id"}), 400
        
        try:
            node_id = int(data['node_id'])
        except (ValueError, TypeError):
            return jsonify({"error": "node_id must be an integer"}), 400
        
        node = get_node_by_id(node_id)
        if not node:
            return jsonify({"error": f"Node {node_id} not found"}), 404
        
        updated_fields = []
        
        if 'status' in data:
            try:
                node.update_status(data['status'])
                updated_fields.append('status')
                if ROS_AVAILABLE and ros_manager and ros_manager.running:
                    ros_manager.send_command(node_id, f"SET_STATUS:{data['status']}")
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
        
        if 'health_score' in data:
            try:
                node.update_health(int(data['health_score']))
                updated_fields.append('health_score')
            except (ValueError, TypeError) as e:
                return jsonify({"error": str(e)}), 400
        
        if not save_system_data(system_data):
            return jsonify({"error": "Failed to persist changes"}), 500
        
        logger.info(f"Node {node_id} updated by {auth.current_user()}: {updated_fields}")
        
        return jsonify({
            "success": True,
            "message": f"Node {node_id} updated successfully",
            "updated_fields": updated_fields,
            "node": node.to_dict()
        }), 200
        
    except Exception as e:
        logger.error(f"Error updating node: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/add_failure', methods=['POST'])
@auth.login_required
@require_json
@limiter.limit("10 per minute")
def add_failure():
    """Log a new failure (requires authentication)"""
    try:
        data = request.get_json()
        
        required = ['node_id', 'description']
        missing = [f for f in required if f not in data]
        if missing:
            return jsonify({"error": f"Missing required fields: {missing}"}), 400
        
        node_id = int(data['node_id'])
        if not get_node_by_id(node_id):
            return jsonify({"error": f"Node {node_id} not found"}), 404
        
        new_failure = {
            "id": len(system_data.get('failures', [])) + 1,
            "timestamp": datetime.now().isoformat(),
            "node_id": node_id,
            "description": data['description'],
            "status": data.get('status', 'open')
        }
        
        system_data.setdefault('failures', []).append(new_failure)
        
        if not save_system_data(system_data):
            system_data['failures'].pop()
            return jsonify({"error": "Failed to persist failure record"}), 500
        
        logger.warning(f"Failure logged for node {node_id}: {data['description']}")
        
        return jsonify({
            "success": True,
            "message": "Failure logged successfully",
            "failure": new_failure
        }), 201
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error adding failure: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "Bad request"}), 400


@app.errorhandler(401)
def unauthorized(e):
    return jsonify({"error": "Unauthorized. Please provide valid credentials."}), 401


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429


@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({"error": "Internal server error"}), 500


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/health')
@limiter.exempt
def health():
    """Health check endpoint"""
    ros_connected = False
    if ROS_AVAILABLE and ros_manager:
        ros_connected = ros_manager.running
        
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "0.1.0",
        "ros_connected": ros_connected
    }), 200


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    # Start ROS Manager if available
    if ROS_AVAILABLE:
        ros_manager = ROS2Manager(ros_update_callback)
        ros_manager.start()
        print("✅ ROS 2 Manager started")
    else:
        print("⚠️  ROS 2 Manager NOT started (Dependencies missing)")

    docker_stats_collector = DockerStatsCollector(
        lambda payload: ros_update_callback('container_metrics', payload),
        logger,
    )
    docker_stats_collector.start()
    
    # Get configuration from environment
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', 5050))
    
    logger.info(f"Starting MicroK3 server on {host}:{port} (debug={debug_mode})")
    
    if debug_mode:
        logger.warning("⚠️  Running in DEBUG mode. Do not use in production!")
    
    if host == '0.0.0.0':
        logger.warning("⚠️  Server exposed on all interfaces (0.0.0.0). Ensure firewall is configured!")
    
    try:
        app.run(debug=debug_mode, host=host, port=port, use_reloader=False)
    finally:
        if ROS_AVAILABLE and ros_manager:
            ros_manager.stop()
        if docker_stats_collector:
            docker_stats_collector.stop()
