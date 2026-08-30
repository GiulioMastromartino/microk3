import json
import os
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

from occupancy_map_viewer import OccupancyMapViewer
from pointcloud_stream import PointCloudAccumulator, PointCloudLiveViewer


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
        self._pointcloud_lock = threading.RLock()
        self._pointcloud_live_viewers: Dict[str, PointCloudLiveViewer] = {}
        self._pointcloud_accumulators: Dict[str, PointCloudAccumulator] = {}
        self._occupancy_map_lock = threading.RLock()
        self._occupancy_map_viewers: Dict[str, OccupancyMapViewer] = {}
        self._pub_lock = threading.RLock()
        self._generic_publishers: Dict[str, Any] = {}
        self._teleop_watchdogs: Dict[str, Any] = {}

        # Publishers (Commands to nodes)
        self.cmd_pub = self.create_publisher(String, "microk3/commands", 10)

        # Publisher for Jetson host diagnostics (JSON over std_msgs/String; mirrors node_status convention)
        try:
            self.jetson_diag_pub = self.create_publisher(
                String, "microk3/jetson_diagnostics", 10
            )
            diag_interval = float(os.environ.get("JETSON_DIAG_PUBLISH_SEC", "2.0"))
            self.create_timer(diag_interval, self.publish_jetson_diagnostics)
        except Exception as exc:
            self.get_logger().warning(f"Jetson diagnostics ROS publisher not created: {exc}")
            self.jetson_diag_pub = None  # type: ignore

        # Subscribers (Telemetry from nodes)
        self.create_subscription(String, "microk3/node_status", self.status_callback, 10)
        self.create_subscription(String, "microk3/system_alerts", self.alert_callback, 10)
        self.create_subscription(String, "microk3/performance_metrics", self.metrics_callback, 10)

        # Joint states cache for arm teleop feedback
        self._joint_states_lock = threading.RLock()
        self._joint_states: Dict[str, Any] = {}
        self._joint_states_stamp: Optional[str] = None
        try:
            from sensor_msgs.msg import JointState as JSMsg  # type: ignore
            self.create_subscription(JSMsg, "/joint_states", self._on_joint_states, 10)
            self.get_logger().info("Subscribed to /joint_states for arm feedback")
        except Exception as exc:
            self.get_logger().warning(f"/joint_states subscription not created: {exc}")

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

    def _on_joint_states(self, msg):
        try:
            with self._joint_states_lock:
                for name, pos in zip(msg.name, msg.position):
                    self._joint_states[name] = float(pos)
                self._joint_states_stamp = datetime.utcnow().isoformat() + "Z"
        except Exception:
            pass

    def get_joint_states(self) -> Dict[str, Any]:
        with self._joint_states_lock:
            return {
                "positions": dict(self._joint_states),
                "stamp": self._joint_states_stamp,
            }

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

    def publish_jetson_diagnostics(self):
        """Publish latest Jetson host diagnostics to ROS graph."""
        if getattr(self, "jetson_diag_pub", None) is None:
            return
        try:
            # Avoid circular import at module load: import app at call time.
            from app import jetson_diag_state  # type: ignore

            if not jetson_diag_state or not jetson_diag_state.get("available"):
                return
            msg = String()
            msg.data = json.dumps(jetson_diag_state)
            self.jetson_diag_pub.publish(msg)
        except Exception as exc:
            self.get_logger().debug(f"Jetson diagnostics publish skipped: {exc}")

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

    def start_pointcloud_live(self, topic_name: str) -> Dict[str, Any]:
        with self._pointcloud_lock:
            if topic_name in self._pointcloud_live_viewers:
                return {"success": True, "topic_name": topic_name, "already_started": True}
            self._pointcloud_live_viewers[topic_name] = PointCloudLiveViewer(self, topic_name)
            return {"success": True, "topic_name": topic_name}

    def stop_pointcloud_live(self, topic_name: str) -> Dict[str, Any]:
        with self._pointcloud_lock:
            viewer = self._pointcloud_live_viewers.pop(topic_name, None)
            if viewer is None:
                return {"success": False, "error": f"Live viewer for {topic_name} is not active"}
            viewer.shutdown()
            return {"success": True, "topic_name": topic_name}

    def get_pointcloud_live_snapshot(self, topic_name: str, max_points: int = 20000):
        with self._pointcloud_lock:
            viewer = self._pointcloud_live_viewers.get(topic_name)
        if viewer is None:
            return {"active": False}
        return viewer.snapshot(max_points)

    def start_pointcloud_accumulation(
        self,
        topic_name: str,
        pose_topic: str = "/zed/zed_node/pose",
        voxel_size: float = 0.05,
        max_map_points: int = 500000,
    ) -> Dict[str, Any]:
        with self._pointcloud_lock:
            if topic_name in self._pointcloud_accumulators:
                return {"success": True, "topic_name": topic_name, "already_started": True}
            self._pointcloud_accumulators[topic_name] = PointCloudAccumulator(
                self,
                topic_name,
                pose_topic=pose_topic,
                voxel_size=voxel_size,
                max_map_points=max_map_points,
            )
            return {"success": True, "topic_name": topic_name, "pose_topic": pose_topic}

    def stop_pointcloud_accumulation(self, topic_name: str) -> Dict[str, Any]:
        with self._pointcloud_lock:
            accumulator = self._pointcloud_accumulators.pop(topic_name, None)
            if accumulator is None:
                return {"success": False, "error": f"Accumulator for {topic_name} is not active"}
            accumulator.shutdown()
            return {"success": True, "topic_name": topic_name}

    def reset_pointcloud_accumulation(self, topic_name: str) -> Dict[str, Any]:
        with self._pointcloud_lock:
            accumulator = self._pointcloud_accumulators.get(topic_name)
        if accumulator is None:
            return {"success": False, "error": f"Accumulator for {topic_name} is not active"}
        accumulator.reset()
        return {"success": True, "topic_name": topic_name}

    def get_pointcloud_accumulation_snapshot(self, topic_name: str):
        with self._pointcloud_lock:
            accumulator = self._pointcloud_accumulators.get(topic_name)
        if accumulator is None:
            return {"active": False}
        return accumulator.snapshot()

    def start_occupancy_map(self, topic_name: str = "/map") -> Dict[str, Any]:
        with self._occupancy_map_lock:
            if topic_name in self._occupancy_map_viewers:
                return {"success": True, "topic_name": topic_name, "already_started": True}
            self._occupancy_map_viewers[topic_name] = OccupancyMapViewer(self, topic_name)
            return {"success": True, "topic_name": topic_name}

    def stop_occupancy_map(self, topic_name: str) -> Dict[str, Any]:
        with self._occupancy_map_lock:
            viewer = self._occupancy_map_viewers.pop(topic_name, None)
            if viewer is None:
                return {"success": False, "error": f"Occupancy map viewer for {topic_name} is not active"}
            viewer.shutdown()
            return {"success": True, "topic_name": topic_name}

    def get_occupancy_map_snapshot(self, topic_name: str):
        with self._occupancy_map_lock:
            viewer = self._occupancy_map_viewers.get(topic_name)
        if viewer is None:
            return {"active": False}
        return viewer.snapshot()

    # ------------------------------------------------------------------
    # Generic publish + Teleop (Publisher Studio / Drive)
    # ------------------------------------------------------------------
    def _fill_message(self, msg: Any, payload: Any) -> None:
        """Recursively fill a ROS message from a dict / primitive."""
        if payload is None:
            return
        if isinstance(payload, dict):
            for key, value in payload.items():
                if not hasattr(msg, key):
                    raise ValueError(f"Unknown field '{key}' for {type(msg).__name__}")
                attr = getattr(msg, key)
                # Nested message?
                if isinstance(value, dict) and hasattr(attr, "__dict__"):
                    self._fill_message(attr, value)
                elif isinstance(value, list):
                    # For arrays: try to set directly or handle typed arrays
                    setattr(msg, key, value)
                else:
                    setattr(msg, key, value)
        else:
            # Primitive wrapper like std_msgs/String.data
            if hasattr(msg, "data"):
                setattr(msg, "data", payload)
            else:
                raise ValueError(f"Payload must be a dict for {type(msg).__name__}")

    def _get_or_create_publisher(self, topic_name: str, topic_type: str):
        key = f"{topic_name}#{topic_type}"
        with self._pub_lock:
            if key in self._generic_publishers:
                return self._generic_publishers[key]
            try:
                msg_cls = get_message(topic_type)
            except Exception as exc:
                raise ValueError(f"Unknown topic type {topic_type}: {exc}") from exc
            pub = self.create_publisher(msg_cls, topic_name, 10)
            self._generic_publishers[key] = (pub, msg_cls)
            return pub, msg_cls

    def publish_generic(self, topic_name: str, topic_type: str, payload: Any) -> Dict[str, Any]:
        """Publish an arbitrary message built from a dict payload."""
        with self._pub_lock:
            pub, msg_cls = self._get_or_create_publisher(topic_name, topic_type)
            msg = msg_cls()
            try:
                self._fill_message(msg, payload)
            except Exception as exc:
                raise ValueError(str(exc)) from exc
            pub.publish(msg)
            return {"success": True, "topic_name": topic_name, "topic_type": topic_type}

    def publish_twist(self, topic_name: str, linear_x: float, angular_z: float,
                      linear_y: float = 0.0, linear_z: float = 0.0,
                      angular_x: float = 0.0, angular_y: float = 0.0) -> Dict[str, Any]:
        """Publish a geometry_msgs/Twist (teleop)."""
        topic_type = "geometry_msgs/msg/Twist"
        with self._pub_lock:
            pub, msg_cls = self._get_or_create_publisher(topic_name, topic_type)
            # Lazily import Twist to avoid hard dep at import time
            try:
                from geometry_msgs.msg import Twist  # type: ignore
                msg = Twist()
            except Exception:
                msg = msg_cls()
            msg.linear.x = float(linear_x)
            msg.linear.y = float(linear_y)
            msg.linear.z = float(linear_z)
            msg.angular.x = float(angular_x)
            msg.angular.y = float(angular_y)
            msg.angular.z = float(angular_z)
            pub.publish(msg)
            # (re)arm watchdog — if no further twist within 0.6s, publish zero
            self._arm_teleop_watchdog(topic_name)
            return {"success": True, "topic_name": topic_name}

    def _arm_teleop_watchdog(self, topic_name: str):
        # Cancel previous
        old = self._teleop_watchdogs.pop(topic_name, None)
        if old is not None:
            try:
                old.cancel()
            except Exception:
                pass

        def _watchdog_fire():
            try:
                self.publish_twist(topic_name, 0.0, 0.0)
                self.get_logger().info(f"Teleop watchdog: zero Twist on {topic_name}")
            except Exception as exc:
                self.get_logger().warning(f"Watchdog publish failed for {topic_name}: {exc}")

        t = threading.Timer(0.6, _watchdog_fire)
        t.daemon = True
        t.start()
        self._teleop_watchdogs[topic_name] = t

    def stop_teleop(self, topic_name: str) -> Dict[str, Any]:
        with self._pub_lock:
            old = self._teleop_watchdogs.pop(topic_name, None)
            if old is not None:
                try:
                    old.cancel()
                except Exception:
                    pass
        # publish zero immediately
        try:
            self.publish_twist(topic_name, 0.0, 0.0)
            # cancel the watchdog we just armed
            with self._pub_lock:
                wd = self._teleop_watchdogs.pop(topic_name, None)
                if wd is not None:
                    try:
                        wd.cancel()
                    except Exception:
                        pass
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "topic_name": topic_name}

    # ------------------------------------------------------------------
    # Arm JointTrajectory publish (rover arm: 5 arm joints + 1 gripper)
    # ------------------------------------------------------------------
    ARM_JOINTS = ['j1_joint', 'j2_joint', 'j3_joint', 'ee_base_joint_x', 'ee_base_joint_y']
    GRIPPER_JOINTS = ['slider_joint']
    JOINT_LIMITS = {
        'j1_joint': None,
        'j2_joint': (-1.5708, 1.5708),
        'j3_joint': (-1.5708, 1.5708),
        'ee_base_joint_x': (0.0, 3.1416),
        'ee_base_joint_y': None,
        'slider_joint': (0.0, 0.05),
    }

    def _clamp_joint(self, name: str, value: float) -> float:
        lim = self.JOINT_LIMITS.get(name)
        if lim is None:
            return float(value)
        return max(lim[0], min(lim[1], float(value)))

    def publish_arm_trajectory(self, positions: Dict[str, float], duration_sec: float = 0.5) -> Dict[str, Any]:
        """Publish JointTrajectory to /arm_controller/joint_trajectory and /gripper_controller/joint_trajectory."""
        # Split arm vs gripper
        arm_positions = {}
        gripper_positions = {}
        for k, v in (positions or {}).items():
            if k in self.ARM_JOINTS:
                arm_positions[k] = self._clamp_joint(k, v)
            elif k in self.GRIPPER_JOINTS:
                gripper_positions[k] = self._clamp_joint(k, v)
            else:
                return {"success": False, "error": f"Unknown joint '{k}'"}

        # Need at least one
        if not arm_positions and not gripper_positions:
            return {"success": False, "error": "No known joints in positions"}

        try:
            from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint  # type: ignore
            from builtin_interfaces.msg import Duration  # type: ignore
        except Exception as exc:
            return {"success": False, "error": f"JointTrajectory type not available: {exc}"}

        duration_ns = int(max(0.05, min(5.0, float(duration_sec))) * 1e9)

        result: Dict[str, Any] = {"success": True}

        if arm_positions:
            # Build full 5-joint trajectory; fill missing joints from current state if available
            with self._joint_states_lock:
                current = dict(self._joint_states)
            joint_names = list(self.ARM_JOINTS)
            positions_list = []
            for jn in joint_names:
                if jn in arm_positions:
                    positions_list.append(arm_positions[jn])
                elif jn in current:
                    positions_list.append(float(current[jn]))
                else:
                    positions_list.append(0.0)
            arm_pub, _ = self._get_or_create_publisher("/arm_controller/joint_trajectory", "trajectory_msgs/msg/JointTrajectory")
            traj = JointTrajectory()
            traj.joint_names = joint_names
            traj.points = [JointTrajectoryPoint(positions=positions_list, time_from_start=Duration(sec=0, nanosec=duration_ns))]
            arm_pub.publish(traj)
            result["arm"] = {"joint_names": joint_names, "positions": positions_list}

        if gripper_positions:
            grip_pub, _ = self._get_or_create_publisher("/gripper_controller/joint_trajectory", "trajectory_msgs/msg/JointTrajectory")
            traj = JointTrajectory()
            traj.joint_names = list(self.GRIPPER_JOINTS)
            val = gripper_positions.get('slider_joint', 0.0)
            traj.points = [JointTrajectoryPoint(positions=[float(val)], time_from_start=Duration(sec=0, nanosec=duration_ns))]
            grip_pub.publish(traj)
            result["gripper"] = {"positions": [float(val)]}

        return result

    def destroy_node(self):
        with self._pointcloud_lock:
            live_viewers = list(self._pointcloud_live_viewers.values())
            accumulators = list(self._pointcloud_accumulators.values())
            self._pointcloud_live_viewers.clear()
            self._pointcloud_accumulators.clear()
        with self._occupancy_map_lock:
            occupancy_map_viewers = list(self._occupancy_map_viewers.values())
            self._occupancy_map_viewers.clear()
        # cancel teleop watchdogs
        for t in list(self._teleop_watchdogs.values()):
            try:
                t.cancel()
            except Exception:
                pass
        self._teleop_watchdogs.clear()
        for viewer in live_viewers:
            viewer.shutdown()
        for accumulator in accumulators:
            accumulator.shutdown()
        for viewer in occupancy_map_viewers:
            viewer.shutdown()
        return super().destroy_node()


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

    def start_pointcloud_live(self, topic_name: str) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.start_pointcloud_live(topic_name)

    def stop_pointcloud_live(self, topic_name: str) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.stop_pointcloud_live(topic_name)

    def get_pointcloud_live_snapshot(self, topic_name: str, max_points: int = 20000):
        with self._lock:
            if not self.running or not self.ros_node:
                return None
            return self.ros_node.get_pointcloud_live_snapshot(topic_name, max_points)

    def start_pointcloud_accumulation(
        self,
        topic_name: str,
        pose_topic: str = "/zed/zed_node/pose",
        voxel_size: float = 0.05,
        max_map_points: int = 500000,
    ) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.start_pointcloud_accumulation(
                topic_name, pose_topic, voxel_size, max_map_points
            )

    def stop_pointcloud_accumulation(self, topic_name: str) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.stop_pointcloud_accumulation(topic_name)

    def reset_pointcloud_accumulation(self, topic_name: str) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.reset_pointcloud_accumulation(topic_name)

    def get_pointcloud_accumulation_snapshot(self, topic_name: str):
        with self._lock:
            if not self.running or not self.ros_node:
                return None
            return self.ros_node.get_pointcloud_accumulation_snapshot(topic_name)

    def start_occupancy_map(self, topic_name: str = "/map") -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.start_occupancy_map(topic_name)

    def stop_occupancy_map(self, topic_name: str) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.stop_occupancy_map(topic_name)

    def get_occupancy_map_snapshot(self, topic_name: str):
        with self._lock:
            if not self.running or not self.ros_node:
                return None
            return self.ros_node.get_occupancy_map_snapshot(topic_name)

    def publish_generic(self, topic_name: str, topic_type: str, payload: Any) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            try:
                return self.ros_node.publish_generic(topic_name, topic_type, payload)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            except Exception as exc:
                return {"success": False, "error": str(exc)}

    def publish_twist(self, topic_name: str, linear_x: float, angular_z: float,
                      linear_y: float = 0.0, linear_z: float = 0.0,
                      angular_x: float = 0.0, angular_y: float = 0.0) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            try:
                return self.ros_node.publish_twist(topic_name, linear_x, angular_z, linear_y, linear_z, angular_x, angular_y)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            except Exception as exc:
                return {"success": False, "error": str(exc)}

    def stop_teleop(self, topic_name: str) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.stop_teleop(topic_name)

    def publish_arm_trajectory(self, positions: Dict[str, float], duration_sec: float = 0.5) -> Dict[str, Any]:
        with self._lock:
            if not self.running or not self.ros_node:
                return {"success": False, "error": "ROS 2 manager is not running"}
            return self.ros_node.publish_arm_trajectory(positions, duration_sec)

    def get_joint_states(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self.running or not self.ros_node:
                return None
            return self.ros_node.get_joint_states()
