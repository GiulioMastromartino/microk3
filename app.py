from flask import Flask, render_template, jsonify, request, abort
from flask_httpauth import HTTPBasicAuth
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import json
import os
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from functools import wraps

from models.node import Node
from config import get_config

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__, template_folder='templates')
app.config.from_object(get_config())

# Setup logging
logs_dir = Path('logs')
logs_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, app.config['LOG_LEVEL']),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(app.config['LOG_FILE']),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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


@auth.verify_password
def verify_password(username: str, password: str) -> Optional[str]:
    """Verify user credentials"""
    if username in users and check_password_hash(users.get(username), password):
        return username
    return None


def load_system_data() -> Dict[str, Any]:
    """Load system data from JSON file"""
    try:
        data_file = Path(app.config['DATA_FILE'])
        if not data_file.exists():
            logger.warning(f"Data file not found: {data_file}. Using default data.")
            return get_default_data()
        
        with open(data_file, 'r') as f:
            data = json.load(f)
            
        # Convert node dictionaries to Node objects
        if 'nodes' in data:
            data['nodes'] = [Node.from_dict(node) for node in data['nodes']]
        
        logger.info(f"Loaded system data from {data_file}")
        return data
        
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


def get_default_data() -> Dict[str, Any]:
    """Return default system data"""
    return {
        "nodes": [
            Node(
                id=1,
                name="Node 1",
                status="active",
                type="STM32H743VIT6",
                ram="1MB",
                flash="2MB",
                cpu="480MHz",
                active_tasks=["Motor Control", "Sensor Fusion"],
                health_score=85,
                uptime="12h 34m",
                network="CAN + Ethernet"
            ),
            Node(
                id=2,
                name="Node 2",
                status="standby",
                type="STM32H743VIT6",
                ram="1MB",
                flash="2MB",
                cpu="480MHz",
                active_tasks=["Navigation"],
                health_score=92,
                uptime="1d 02h",
                network="CAN + Ethernet"
            ),
            Node(
                id=3,
                name="Node 3",
                status="active",
                type="STM32H743VIT6",
                ram="1MB",
                flash="2MB",
                cpu="480MHz",
                active_tasks=["Vision"],
                health_score=78,
                uptime="1d 05h",
                network="CAN + Ethernet"
            )
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


# Load system data at startup
system_data = load_system_data()


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
    for node in system_data['nodes']:
        if node.id == node_id:
            return node
    return None


# ============================================================================
# WEB ROUTES
# ============================================================================

@app.route('/')
def index():
    """Main dashboard page"""
    # Convert Node objects to dicts for template
    template_data = system_data.copy()
    template_data['nodes'] = [node.to_dict() for node in system_data['nodes']]
    return render_template('index.html', system_data=template_data)


@app.route('/nodes')
def nodes():
    """Nodes detail page"""
    template_data = system_data.copy()
    template_data['nodes'] = [node.to_dict() for node in system_data['nodes']]
    return render_template('node.html', system_data=template_data)


# ============================================================================
# API ROUTES (Read-only, no auth required)
# ============================================================================

@app.route('/api/system_status')
@limiter.limit("30 per minute")
def api_system_status():
    """Get system status summary"""
    try:
        active_nodes = sum(1 for n in system_data['nodes'] if n.is_active)
        
        return jsonify({
            "status": system_data.get("system_status", "unknown"),
            "nodes_online": active_nodes,
            "total_nodes": len(system_data['nodes']),
            "tasks_running": len(system_data.get('tasks', {})),
            "network_latency": 12,
            "timestamp": datetime.now().isoformat()
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


@app.route('/api/tasks')
@limiter.limit("30 per minute")
def api_tasks():
    """Get task status"""
    try:
        return jsonify(system_data.get('tasks', {})), 200
    except Exception as e:
        logger.error(f"Error getting tasks: {e}")
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
        
        # Validate required fields
        if 'node_id' not in data:
            return jsonify({"error": "Missing required field: node_id"}), 400
        
        # Validate node_id type
        try:
            node_id = int(data['node_id'])
        except (ValueError, TypeError):
            return jsonify({"error": "node_id must be an integer"}), 400
        
        # Find node
        node = get_node_by_id(node_id)
        if not node:
            return jsonify({"error": f"Node {node_id} not found"}), 404
        
        # Update fields
        updated_fields = []
        
        if 'status' in data:
            try:
                node.update_status(data['status'])
                updated_fields.append('status')
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
        
        if 'health_score' in data:
            try:
                node.update_health(int(data['health_score']))
                updated_fields.append('health_score')
            except (ValueError, TypeError) as e:
                return jsonify({"error": str(e)}), 400
        
        # Save changes
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
        
        # Validate required fields
        required = ['node_id', 'description']
        missing = [f for f in required if f not in data]
        if missing:
            return jsonify({"error": f"Missing required fields: {missing}"}), 400
        
        # Validate node exists
        node_id = int(data['node_id'])
        if not get_node_by_id(node_id):
            return jsonify({"error": f"Node {node_id} not found"}), 404
        
        # Create failure record
        new_failure = {
            "id": len(system_data.get('failures', [])) + 1,
            "timestamp": datetime.now().isoformat(),
            "node_id": node_id,
            "description": data['description'],
            "status": data.get('status', 'open')
        }
        
        system_data.setdefault('failures', []).append(new_failure)
        
        # Save changes
        if not save_system_data(system_data):
            system_data['failures'].pop()  # Rollback
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
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "0.1.0"
    }), 200


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    # Get configuration from environment
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', 5050))
    
    logger.info(f"Starting MicroK3 server on {host}:{port} (debug={debug_mode})")
    
    if debug_mode:
        logger.warning("⚠️  Running in DEBUG mode. Do not use in production!")
    
    if host == '0.0.0.0':
        logger.warning("⚠️  Server exposed on all interfaces (0.0.0.0). Ensure firewall is configured!")
    
    app.run(debug=debug_mode, host=host, port=port)
