"""Dedicated PointCloud2 streaming helpers for the dashboard."""

from collections import deque
import threading

import numpy as np
from geometry_msgs.msg import PoseStamped
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2, PointField
from sensor_msgs_py import point_cloud2


def parse_cloud_frame(msg: PointCloud2, logger=None):
    """Extract XYZ and optional packed RGB values from a PointCloud2 frame."""
    rgb_field = next((field for field in msg.fields if field.name == "rgb"), None)
    xyz = point_cloud2.read_points_numpy(
        msg, field_names=("x", "y", "z"), skip_nans=False
    ).astype(np.float32, copy=False)
    valid_points = np.isfinite(xyz).all(axis=1)
    xyz = xyz[valid_points]

    if xyz.shape[0] == 0 or rgb_field is None:
        return xyz, None, False

    # Read RGB separately so UINT32 RGB remains compatible with FLOAT32 XYZ.
    raw_rgb = point_cloud2.read_points_numpy(
        msg, field_names=("rgb",), skip_nans=False
    ).reshape(-1)[valid_points]

    if rgb_field.datatype == PointField.FLOAT32:
        rgb_uint = raw_rgb.astype(np.float32).view(np.uint32)
    elif rgb_field.datatype in (PointField.UINT32, PointField.INT32):
        rgb_uint = raw_rgb.astype(np.uint32)
    else:
        if logger:
            logger.warning(
                f"Ignoring rgb field with unsupported datatype {rgb_field.datatype}"
            )
        return xyz, None, False

    colors = np.stack(
        [
            (rgb_uint >> 16) & 0xFF,
            (rgb_uint >> 8) & 0xFF,
            rgb_uint & 0xFF,
        ],
        axis=1,
    ).astype(np.uint8)
    return xyz, colors, True


class PointCloudLiveViewer:
    """Keep only the latest point-cloud frame in the camera-local frame."""

    def __init__(self, node, cloud_topic):
        self.node = node
        self.cloud_topic = cloud_topic
        self._lock = threading.RLock()
        self._latest = None
        self._frames_received = 0
        self._last_error = None
        self._sub = node.create_subscription(
            PointCloud2, cloud_topic, self._on_cloud, qos_profile_sensor_data
        )

    def _on_cloud(self, msg):
        try:
            xyz, colors, has_color = parse_cloud_frame(msg, self.node.get_logger())
            with self._lock:
                self._latest = {
                    "xyz": xyz,
                    "colors": colors,
                    "has_color": has_color,
                    "frame_id": msg.header.frame_id,
                    "point_count": int(xyz.shape[0]),
                }
                self._frames_received += 1
                self._last_error = None
        except Exception as exc:
            self.node.get_logger().error(f"Live point-cloud parsing failed: {exc}")
            with self._lock:
                self._last_error = str(exc)

    def snapshot(self, max_points=20000):
        with self._lock:
            if self._latest is None:
                return None
            data = {
                **self._latest,
                "xyz": self._latest["xyz"].copy(),
                "colors": (
                    self._latest["colors"].copy()
                    if self._latest["colors"] is not None
                    else None
                ),
                "frames_received": self._frames_received,
                "last_error": self._last_error,
            }

        point_count = data["point_count"]
        if point_count > max_points:
            stride = max(1, -(-point_count // max_points))
            data["xyz"] = data["xyz"][::stride]
            if data["colors"] is not None:
                data["colors"] = data["colors"][::stride]
        data["point_count_after_downsample"] = int(data["xyz"].shape[0])
        return data

    def shutdown(self):
        self.node.destroy_subscription(self._sub)


class PointCloudAccumulator:
    """Accumulate camera-local clouds using the nearest ZED camera pose."""

    def __init__(
        self,
        node,
        cloud_topic,
        pose_topic="/zed/zed_node/pose",
        voxel_size=0.05,
        max_map_points=500_000,
        merge_interval_sec=1.0,
        max_time_diff_sec=0.1,
        pose_buffer_maxlen=100,
    ):
        self.node = node
        self.cloud_topic = cloud_topic
        self.pose_topic = pose_topic
        self.voxel_size = voxel_size
        self.max_map_points = max_map_points
        self.max_time_diff_sec = max_time_diff_sec
        self._lock = threading.RLock()
        self._pose_buffer = deque(maxlen=pose_buffer_maxlen)
        self._pending_points = []
        self._pending_colors = []
        self._map_points = np.zeros((0, 3), dtype=np.float32)
        self._map_colors = None
        self._has_color = False
        self._fixed_frame_id = None
        self._frames_received = 0
        self._frames_dropped_no_pose = 0
        self._last_error = None
        self._cloud_sub = node.create_subscription(
            PointCloud2, cloud_topic, self._on_cloud, qos_profile_sensor_data
        )
        self._pose_sub = node.create_subscription(
            PoseStamped, pose_topic, self._on_pose, qos_profile_sensor_data
        )
        self._timer = node.create_timer(merge_interval_sec, self._merge_pending)

    def _on_pose(self, msg):
        with self._lock:
            self._pose_buffer.append(msg)

    @staticmethod
    def _stamp_to_sec(stamp):
        return stamp.sec + stamp.nanosec * 1e-9

    def _find_nearest_pose(self, target_stamp):
        target_time = self._stamp_to_sec(target_stamp)
        with self._lock:
            poses = list(self._pose_buffer)

        nearest = None
        nearest_difference = None
        for pose_msg in poses:
            difference = abs(self._stamp_to_sec(pose_msg.header.stamp) - target_time)
            if nearest_difference is None or difference < nearest_difference:
                nearest = pose_msg
                nearest_difference = difference

        if nearest is None or nearest_difference is None:
            return None
        if nearest_difference > self.max_time_diff_sec:
            return None
        return nearest

    def _on_cloud(self, msg):
        try:
            xyz_local, colors, has_color = parse_cloud_frame(msg, self.node.get_logger())
            if xyz_local.shape[0] == 0:
                return

            pose_msg = self._find_nearest_pose(msg.header.stamp)
            if pose_msg is None:
                with self._lock:
                    self._frames_dropped_no_pose += 1
                    self._last_error = (
                        "No ZED pose within the timestamp tolerance; ensure the pose "
                        "topic is played alongside the point cloud."
                    )
                return

            rotation, translation = self._matrix_from_pose(pose_msg)
            xyz_world = (
                xyz_local.astype(np.float64) @ rotation.T + translation
            ).astype(np.float32)
            with self._lock:
                self._pending_points.append(xyz_world)
                self._pending_colors.append(colors)
                self._frames_received += 1
                self._has_color = self._has_color or has_color
                self._fixed_frame_id = pose_msg.header.frame_id
                self._last_error = None
        except Exception as exc:
            self.node.get_logger().error(f"Point-cloud accumulation failed: {exc}")
            with self._lock:
                self._last_error = str(exc)

    @staticmethod
    def _matrix_from_pose(pose_stamped):
        position = pose_stamped.pose.position
        quaternion = pose_stamped.pose.orientation
        x, y, z, w = quaternion.x, quaternion.y, quaternion.z, quaternion.w
        rotation = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
            ],
            dtype=np.float64,
        )
        translation = np.array([position.x, position.y, position.z], dtype=np.float64)
        return rotation, translation

    def _merge_pending(self):
        with self._lock:
            if not self._pending_points:
                return
            pending_points = self._pending_points
            pending_colors = self._pending_colors
            self._pending_points = []
            self._pending_colors = []
            map_points = self._map_points
            map_colors = self._map_colors
            has_color = self._has_color

        new_points = np.concatenate(pending_points, axis=0)
        new_colors = None
        if has_color:
            new_colors = np.concatenate(
                [
                    color
                    if color is not None
                    else np.zeros((points.shape[0], 3), dtype=np.uint8)
                    for color, points in zip(pending_colors, pending_points)
                ],
                axis=0,
            )
            if map_colors is None:
                map_colors = np.zeros((map_points.shape[0], 3), dtype=np.uint8)

        merged_points = np.concatenate([map_points, new_points], axis=0)
        merged_colors = (
            np.concatenate([map_colors, new_colors], axis=0) if has_color else None
        )
        voxel_size = self.voxel_size
        while True:
            downsampled_points, downsampled_colors = self._voxel_downsample(
                merged_points, merged_colors, voxel_size
            )
            if downsampled_points.shape[0] <= self.max_map_points:
                break
            voxel_size *= 2.0
            self.node.get_logger().warning(
                f"Map exceeded max_map_points; voxel size increased to {voxel_size:.3f}m"
            )

        with self._lock:
            self._map_points = downsampled_points
            self._map_colors = downsampled_colors

    @staticmethod
    def _voxel_downsample(points, colors, voxel_size):
        if points.shape[0] == 0:
            return points, colors
        keys = np.floor(points / voxel_size).astype(np.int64)
        keys_view = np.ascontiguousarray(keys).view(
            np.dtype((np.void, keys.dtype.itemsize * keys.shape[1]))
        )
        _, unique_indices = np.unique(keys_view, return_index=True)
        return (
            points[unique_indices],
            colors[unique_indices] if colors is not None else None,
        )

    def snapshot(self):
        with self._lock:
            return {
                "points": self._map_points.copy(),
                "colors": self._map_colors.copy() if self._map_colors is not None else None,
                "has_color": self._has_color,
                "frame_id": self._fixed_frame_id,
                "point_count": int(self._map_points.shape[0]),
                "frames_received": self._frames_received,
                "frames_dropped_no_pose": self._frames_dropped_no_pose,
                "last_error": self._last_error,
            }

    def reset(self):
        with self._lock:
            self._map_points = np.zeros((0, 3), dtype=np.float32)
            self._map_colors = None
            self._has_color = False
            self._fixed_frame_id = None
            self._pending_points.clear()
            self._pending_colors.clear()
            self._frames_received = 0
            self._frames_dropped_no_pose = 0
            self._last_error = None

    def shutdown(self):
        self.node.destroy_subscription(self._cloud_sub)
        self.node.destroy_subscription(self._pose_sub)
        self.node.destroy_timer(self._timer)