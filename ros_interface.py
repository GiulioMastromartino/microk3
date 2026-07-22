import json
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import rclpy
from rclpy.node import Node as RosNode
from rosidl_runtime_py.convert import message_to_ordereddict
from rosidl_runtime_py.utilities import get_message
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Float32, Int32, String


def _coerce_jsonable(value: Any) -> Any:
    """Recursively normalize ROS values into JSON-serializable types."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _coerce_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def serialize_ros_message(msg: Any) -> Dict[str, Any]:
    """Convert an arbitrary ROS message into structured JSON plus fallback text."""
    try:
        structured = _coerce_jsonable(message_to_ordereddict(msg))
    except Exception:
        structured = None

    try:
        raw_text = str(msg)
    except Exception:
        raw_text = repr(msg)

    pretty_json = None
    if structured is not None:
        try:
            pretty_json = json.dumps(structured, indent=2, sort_keys=False)
        except Exception:
            pretty_json = None

    return {
        "structured": structured,
        "text": raw_text,
        "pretty": pretty_json or raw_text,
    }


class MicroK3RosNode(RosNode):
    GRAPH_REFRESH_SEC = 3.0

    def __init__(self, app_state_callback):
        super().__init__("microk3_dashboard")
        self.app_state_callback = app_state_callback
        self._watch_lock = threading.RLock()
        self._watched_topics: Dict[str, Dict[str, Any]] = {}

        # Publishers (Commands to nodes)
        self.cmd_pub = self.create_publisher(String, "microk3/commands", 10)

        # Subscribers (Telemetry from nodes)
        self.create_subscription(String, "microk3/node_status", self.status_callback, 10)
        self.create_subscription(String, "microk3/system_alerts", self.alert_callback, 10)
        self.create_subscription(String, "microk3/performance_metrics", self.metrics_callback, 10)

        self.create_timer(self.GRAPH_REFRESH_SEC, self.publish_graph_snapshot)
        self.get_logger().info("MicroK3 Dashboard Node Started")

    def status_callback(self, msg):
        try:
            data = json.loads(msg.data)
            if "heartbeat_raw" in data:
                self.app_state_callback("raw_heartbeat", data)
            self.app_state_callback("update_node", data)
        except json.JSONDecodeError:
            self.get_logger().error(f"Invalid JSON in status: {msg.data}")

    def alert_callback(self, msg):
        try:
            data = json.loads(msg.data)
            self.app_state_callback("add_failure", data)
        except json.JSONDecodeError:
            self.get_logger().error(f"Invalid JSON in alert: {msg.data}")

    def metrics_callback(self, msg):
        try:
            data = json.loads(msg.data)
            self.app_state_callback("performance_metrics", data)
        except json.JSONDecodeError:
            self.get_logger().error(f"Invalid JSON in metrics: {msg.data}")

    def send_command(self, node_id, command):
        msg = String()
        msg.data = json.dumps({"target_id": node_id, "command": command})
        self.cmd_pub.publish(msg)
        self.get_logger().info(f"Sent command: {msg.data}")

    def publish_graph_snapshot(self):
        try:
            nodes = [
                {"name": name, "namespace": namespace}
                for name, namespace in self.get_node_names_and_namespaces()
            ]
            nodes.sort(key=lambda item: (item["namespace"], item["name"]))

            watched = set(self._watched_topics.keys())
            topics = [
                {"name": name, "types": list(types), "watched": name in watched}
                for name, types in self.get_topic_names_and_types()
            ]
            topics.sort(key=lambda item: item["name"])

            self.app_state_callback(
                "graph_snapshot",
                {
                    "nodes": nodes,
                    "topics": topics,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        except Exception as exc:
            self.get_logger().error(f"Failed to snapshot ROS graph: {exc}")
            self.app_state_callback(
                "graph_snapshot_error",
                {
                    "error": str(exc),
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

    def _topic_type_map(self) -> Dict[str, List[str]]:
        return {name: list(types) for name, types in self.get_topic_names_and_types()}

    def watch_topic(self, topic_name: str) -> Dict[str, Any]:
        with self._watch_lock:
            if topic_name in self._watched_topics:
                return {"success": True, "topic_name": topic_name, "already_watched": True}

            topic_types = self._topic_type_map().get(topic_name)
            if not topic_types:
                return {"success": False, "error": f"Topic {topic_name} not found"}

            topic_type = topic_types[0]
            try:
                message_cls = get_message(topic_type)
            except Exception as exc:
                error = f"Failed to resolve message type {topic_type}: {exc}"
                self.app_state_callback(
                    "watch_error",
                    {"topic_name": topic_name, "topic_type": topic_type, "error": error},
                )
                return {"success": False, "error": error}

            try:
                subscription = self.create_subscription(
                    message_cls,
                    topic_name,
                    self._build_dynamic_callback(topic_name, topic_type),
                    10,
                )
            except Exception as exc:
                error = f"Failed to subscribe to {topic_name}: {exc}"
                self.app_state_callback(
                    "watch_error",
                    {"topic_name": topic_name, "topic_type": topic_type, "error": error},
                )
                return {"success": False, "error": error}

            self._watched_topics[topic_name] = {
                "topic_name": topic_name,
                "topic_type": topic_type,
                "subscription": subscription,
            }
            self.app_state_callback(
                "watch_started",
                {"topic_name": topic_name, "topic_type": topic_type},
            )
            self.publish_graph_snapshot()
            return {"success": True, "topic_name": topic_name, "topic_type": topic_type}

    def unwatch_topic(self, topic_name: str) -> Dict[str, Any]:
        with self._watch_lock:
            watched = self._watched_topics.pop(topic_name, None)
            if watched is None:
                return {"success": False, "error": f"Topic {topic_name} is not being watched"}

            try:
                self.destroy_subscription(watched["subscription"])
            except Exception as exc:
                self.get_logger().warning(f"Failed to destroy subscription for {topic_name}: {exc}")

            self.app_state_callback("watch_stopped", {"topic_name": topic_name})
            self.publish_graph_snapshot()
            return {"success": True, "topic_name": topic_name}

    def _build_dynamic_callback(self, topic_name: str, topic_type: str):
        def _callback(msg):
            payload = serialize_ros_message(msg)
            self.app_state_callback(
                "topic_sample",
                {
                    "topic_name": topic_name,
                    "topic_type": topic_type,
                    "sample": payload,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

        return _callback


class ROS2Manager:
    def __init__(self, update_callback):
        self.ros_node: Optional[MicroK3RosNode] = None
        self.executor = None
        self.thread = None
        self.update_callback = update_callback
        self.running = False
        self._lock = threading.RLock()

    def start(self):
        if not rclpy.ok():
            rclpy.init()

        self.ros_node = MicroK3RosNode(self.update_callback)
        self.executor = rclpy.executors.MultiThreadedExecutor()
        self.executor.add_node(self.ros_node)

        self.running = True
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()
        self.ros_node.publish_graph_snapshot()

    def _spin(self):
        try:
            self.executor.spin()
        except Exception as exc:
            print(f"ROS 2 Spin Error: {exc}")
        finally:
            self.running = False

    def stop(self):
        self.running = False
        if self.executor:
            self.executor.shutdown()
        if self.ros_node:
            self.ros_node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    def send_command(self, node_id, command):
        if self.ros_node:
            self.ros_node.send_command(node_id, command)
            return True
        return False

    def watch_topic(self, topic_name: str) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.watch_topic(topic_name)

    def unwatch_topic(self, topic_name: str) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.unwatch_topic(topic_name)
