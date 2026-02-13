import rclpy
from rclpy.node import Node as RosNode
from std_msgs.msg import String, Int32, Float32
from sensor_msgs.msg import BatteryState
import threading
import json
import time

class MicroK3RosNode(RosNode):
    def __init__(self, app_state_callback):
        super().__init__('microk3_dashboard')
        self.app_state_callback = app_state_callback
        
        # Publishers (Commands to nodes)
        self.cmd_pub = self.create_publisher(String, 'microk3/commands', 10)
        
        # Subscribers (Telemetry from nodes)
        self.create_subscription(String, 'microk3/node_status', self.status_callback, 10)
        self.create_subscription(String, 'microk3/system_alerts', self.alert_callback, 10)
        
        self.get_logger().info('MicroK3 Dashboard Node Started')

    def status_callback(self, msg):
        try:
            # Expecting JSON: {"id": 1, "status": "active", "health": 95, "uptime": "12h"}
            data = json.loads(msg.data)
            self.app_state_callback('update_node', data)
        except json.JSONDecodeError:
            self.get_logger().error(f'Invalid JSON in status: {msg.data}')

    def alert_callback(self, msg):
        try:
            # Expecting JSON: {"node_id": 1, "msg": "Overheating", "level": "warning"}
            data = json.loads(msg.data)
            self.app_state_callback('add_failure', data)
        except json.JSONDecodeError:
            self.get_logger().error(f'Invalid JSON in alert: {msg.data}')

    def send_command(self, node_id, command):
        msg = String()
        msg.data = json.dumps({"target_id": node_id, "command": command})
        self.cmd_pub.publish(msg)
        self.get_logger().info(f'Sent command: {msg.data}')

class ROS2Manager:
    def __init__(self, update_callback):
        self.ros_node = None
        self.executor = None
        self.thread = None
        self.update_callback = update_callback
        self.running = False

    def start(self):
        if not rclpy.ok():
            rclpy.init()
        
        self.ros_node = MicroK3RosNode(self.update_callback)
        self.executor = rclpy.executors.MultiThreadedExecutor()
        self.executor.add_node(self.ros_node)
        
        self.running = True
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def _spin(self):
        try:
            self.executor.spin()
        except Exception as e:
            print(f"ROS 2 Spin Error: {e}")
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
