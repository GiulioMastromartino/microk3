import base64
import threading

import numpy as np
from nav_msgs.msg import OccupancyGrid
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy


OCCUPANCY_GRID_QOS = QoSProfile(
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
)


class OccupancyMapViewer:
    """Keep the most recent OccupancyGrid without using point-cloud code."""

    def __init__(self, node, topic_name="/map"):
        self.node = node
        self.topic_name = topic_name
        self._lock = threading.RLock()
        self._latest = None
        self._frames_received = 0
        self._last_error = None
        self._sub = node.create_subscription(
            OccupancyGrid, topic_name, self._on_map, OCCUPANCY_GRID_QOS
        )

    def _on_map(self, msg: OccupancyGrid):
        try:
            width = int(msg.info.width)
            height = int(msg.info.height)
            data = np.array(msg.data, dtype=np.int8)
            if data.size != width * height:
                raise ValueError(
                    f"Expected {width * height} cells, received {data.size}"
                )
            with self._lock:
                self._latest = {
                    "width": width,
                    "height": height,
                    "resolution": float(msg.info.resolution),
                    "origin": {
                        "x": float(msg.info.origin.position.x),
                        "y": float(msg.info.origin.position.y),
                        "z": float(msg.info.origin.position.z),
                    },
                    "data": data,
                    "frame_id": msg.header.frame_id,
                }
                self._frames_received += 1
                self._last_error = None
        except Exception as exc:
            self.node.get_logger().error(f"Errore parsing OccupancyGrid: {exc}")
            with self._lock:
                self._last_error = str(exc)

    def snapshot(self):
        with self._lock:
            if self._latest is None:
                return None
            data = dict(self._latest)
            frames_received = self._frames_received
            last_error = self._last_error

        grid = data.pop("data")
        data["data_base64"] = base64.b64encode(grid.tobytes()).decode("ascii")
        data["frames_received"] = frames_received
        data["last_error"] = last_error
        return data

    def shutdown(self):
        self.node.destroy_subscription(self._sub)