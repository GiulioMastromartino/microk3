#!/usr/bin/env python3

import json
import os
import time
from collections import deque
from datetime import datetime
from statistics import pstdev
from typing import Deque, Dict, Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy
from std_msgs.msg import Int32
from std_msgs.msg import String


def utc_timestamp() -> str:
    return datetime.utcnow().isoformat() + "Z"


class TopicTelemetryState:
    WINDOW_SIZE = 30

    def __init__(self, logical_topic: str):
        self.logical_topic = logical_topic
        self.last_receive_monotonic: Optional[float] = None
        self.last_sequence: Optional[int] = None
        self.last_publish_us: Optional[int] = None
        self.last_value = None
        self.sequence_gaps = 0
        self.client_to_agent_ms: Optional[float] = None
        self.agent_to_ros_ms: Optional[float] = None
        self.end_to_end_ms: Optional[float] = None
        self.raw_delta_ms: Optional[float] = None
        self.client_to_agent_jitter_ms = 0.0
        self.agent_to_ros_jitter_ms = 0.0
        self.end_to_end_jitter_ms = 0.0
        self.rate_hz = 0.0
        self.bandwidth_bps = 0.0
        self._samples: Deque[Dict[str, float]] = deque(maxlen=self.WINDOW_SIZE)
        self._publish_us_samples: Deque[float] = deque(maxlen=self.WINDOW_SIZE)
        self._client_mapping_samples: Deque[Dict[str, float]] = deque(maxlen=self.WINDOW_SIZE)
        self._client_to_agent_samples_ms: Deque[float] = deque(maxlen=self.WINDOW_SIZE)
        self._agent_to_ros_samples_ms: Deque[float] = deque(maxlen=self.WINDOW_SIZE)
        self._end_to_end_samples_ms: Deque[float] = deque(maxlen=self.WINDOW_SIZE)

    def update(
        self,
        sample: Dict[str, object],
        now_monotonic: float,
        payload_bytes: int,
        clock_scale: Optional[float],
        clock_intercept_us: Optional[float],
        raw_receive_monotonic: Optional[float] = None,
    ) -> None:
        sequence = int(sample.get("seq", 0))
        publish_us = int(sample.get("publish_us", 0))
        value = sample.get("value")
        receive_us = now_monotonic * 1000000.0
        valid_publish_time = publish_us > 0
        if valid_publish_time:
            self.raw_delta_ms = (receive_us - float(publish_us)) / 1000.0
        else:
            self.raw_delta_ms = None

        if self.last_sequence is not None and sequence > self.last_sequence + 1:
            self.sequence_gaps += sequence - self.last_sequence - 1
        self.client_to_agent_ms = None
        self.agent_to_ros_ms = None
        self.end_to_end_ms = None

        estimated_host_publish_us: Optional[float] = None
        if (
            valid_publish_time and
            clock_scale is not None and
            clock_intercept_us is not None
        ):
            estimated_host_publish_us = (float(publish_us) * clock_scale) + clock_intercept_us

        if estimated_host_publish_us is not None:
            full_path_ms = self._normalize_clock_mapped_latency_ms((receive_us - estimated_host_publish_us) / 1000.0)
            self.client_to_agent_ms = full_path_ms
            if raw_receive_monotonic is not None:
                raw_receive_us = raw_receive_monotonic * 1000000.0
                self._client_mapping_samples.append({
                    "publish_us": float(publish_us),
                    "raw_receive_us": raw_receive_us,
                })
                predicted_raw_receive_us = self._map_publish_to_raw_receive_us(float(publish_us))
                if predicted_raw_receive_us is not None:
                    self.client_to_agent_ms = max(
                        0.0,
                        (raw_receive_us - predicted_raw_receive_us) / 1000.0,
                    )
                else:
                    self.client_to_agent_ms = self._normalize_clock_mapped_latency_ms(
                        (raw_receive_us - estimated_host_publish_us) / 1000.0
                    )
                self.agent_to_ros_ms = self._normalize_direct_latency_ms(
                    (receive_us - raw_receive_us) / 1000.0
                )
                if self.client_to_agent_ms is None and full_path_ms is not None and self.agent_to_ros_ms is not None:
                    self.client_to_agent_ms = max(0.0, full_path_ms - self.agent_to_ros_ms)
                if self.client_to_agent_ms is not None and self.agent_to_ros_ms is not None:
                    self.end_to_end_ms = self.client_to_agent_ms + self.agent_to_ros_ms
                elif full_path_ms is not None:
                    self.end_to_end_ms = full_path_ms

        self._update_jitter_series(
            self._client_to_agent_samples_ms,
            self.client_to_agent_ms,
            "client_to_agent_jitter_ms",
        )
        self._update_jitter_series(
            self._agent_to_ros_samples_ms,
            self.agent_to_ros_ms,
            "agent_to_ros_jitter_ms",
        )
        self._update_jitter_series(
            self._end_to_end_samples_ms,
            self.end_to_end_ms,
            "end_to_end_jitter_ms",
        )

        self._samples.append({
            "receive_monotonic": now_monotonic,
            "bytes": float(payload_bytes),
        })
        if valid_publish_time:
            self._publish_us_samples.append(float(publish_us))
        if len(self._samples) > 1:
            elapsed = self._samples[-1]["receive_monotonic"] - self._samples[0]["receive_monotonic"]
            if elapsed > 0.0:
                self.bandwidth_bps = sum(entry["bytes"] for entry in self._samples) / elapsed
        if len(self._publish_us_samples) > 1:
            publish_elapsed_sec = (self._publish_us_samples[-1] - self._publish_us_samples[0]) / 1000000.0
            if publish_elapsed_sec > 0.0:
                self.rate_hz = (len(self._publish_us_samples) - 1) / publish_elapsed_sec
        elif len(self._samples) > 1:
            elapsed = self._samples[-1]["receive_monotonic"] - self._samples[0]["receive_monotonic"]
            if elapsed > 0.0:
                self.rate_hz = (len(self._samples) - 1) / elapsed

        self.last_receive_monotonic = now_monotonic
        self.last_sequence = sequence
        self.last_publish_us = publish_us
        self.last_value = value

    def as_dict(self, now_monotonic: float) -> Dict[str, object]:
        stale_sec = None
        if self.last_receive_monotonic is not None:
            stale_sec = now_monotonic - self.last_receive_monotonic
        return {
            "topic": self.logical_topic,
            "last_seq": self.last_sequence,
            "last_value": self.last_value,
            "last_publish_us": self.last_publish_us,
            "client_to_agent_ms": round(self.client_to_agent_ms, 3) if self.client_to_agent_ms is not None else None,
            "client_to_agent_jitter_ms": round(self.client_to_agent_jitter_ms, 3),
            "agent_to_ros_ms": round(self.agent_to_ros_ms, 3) if self.agent_to_ros_ms is not None else None,
            "agent_to_ros_jitter_ms": round(self.agent_to_ros_jitter_ms, 3),
            "end_to_end_ms": round(self.end_to_end_ms, 3) if self.end_to_end_ms is not None else None,
            "end_to_end_jitter_ms": round(self.end_to_end_jitter_ms, 3),
            "lag_ms": round(self.end_to_end_ms, 3) if self.end_to_end_ms is not None else None,
            "raw_delta_ms": round(self.raw_delta_ms, 3) if self.raw_delta_ms is not None else None,
            "jitter_ms": round(self.end_to_end_jitter_ms, 3),
            "rate_hz": round(self.rate_hz, 3),
            "bandwidth_bps": round(self.bandwidth_bps, 3),
            "sequence_gaps": self.sequence_gaps,
            "stale": stale_sec is None or stale_sec > 2.0,
            "last_rx_age_sec": round(stale_sec, 3) if stale_sec is not None else None,
        }

    @staticmethod
    def _normalize_clock_mapped_latency_ms(value_ms: float) -> Optional[float]:
        if value_ms >= 0.0:
            return value_ms
        if abs(value_ms) <= 250.0:
            return 0.0
        return None

    @staticmethod
    def _normalize_direct_latency_ms(value_ms: float) -> Optional[float]:
        if value_ms >= 0.0:
            return value_ms
        if abs(value_ms) <= 5.0:
            return 0.0
        return None

    def _update_jitter_series(self, sample_window: Deque[float], value_ms: Optional[float], attr_name: str) -> None:
        if value_ms is not None:
            sample_window.append(value_ms)
        jitter_value = pstdev(sample_window) if len(sample_window) > 1 else 0.0
        setattr(self, attr_name, jitter_value)

    def _map_publish_to_raw_receive_us(self, publish_us: float) -> Optional[float]:
        if not self._client_mapping_samples:
            return None

        samples = list(self._client_mapping_samples)
        if len(samples) == 1:
            single = samples[0]
            return publish_us + (single["raw_receive_us"] - single["publish_us"])

        mean_publish = sum(sample["publish_us"] for sample in samples) / len(samples)
        mean_receive = sum(sample["raw_receive_us"] for sample in samples) / len(samples)
        variance_publish = sum(
            (sample["publish_us"] - mean_publish) ** 2 for sample in samples
        )
        if variance_publish <= 0.0:
            baseline = min(
                sample["raw_receive_us"] - sample["publish_us"] for sample in samples
            )
            return publish_us + baseline

        covariance = sum(
            (sample["publish_us"] - mean_publish) * (sample["raw_receive_us"] - mean_receive)
            for sample in samples
        )
        scale = covariance / variance_publish
        if scale <= 0.0:
            scale = 1.0
        intercept = min(
            sample["raw_receive_us"] - (scale * sample["publish_us"])
            for sample in samples
        )
        return (scale * publish_us) + intercept


class RenodeHeartbeatBridge(Node):
    def __init__(self):
        super().__init__("renode_heartbeat_bridge")

        self.node_id = int(os.environ.get("RENODE_NODE_ID", "755"))
        self.node_name = os.environ.get("RENODE_NODE_NAME", "stm32h755")
        self.node_type = os.environ.get("RENODE_NODE_TYPE", "STM32H755")
        self.node_network = os.environ.get("RENODE_NODE_NETWORK", "Ethernet")
        self.heartbeat_topic = os.environ.get("RENODE_HEARTBEAT_TOPIC", "heartbeat")
        self.heartbeat_telemetry_topic = os.environ.get(
            "RENODE_HEARTBEAT_TELEMETRY_TOPIC",
            "heartbeat_telemetry",
        )
        self.position_telemetry_topic = os.environ.get(
            "RENODE_POSITION_TELEMETRY_TOPIC",
            "",
        )
        self.time_sync_request_topic = os.environ.get(
            "MICROK3_TIME_SYNC_REQUEST_TOPIC",
            "time_sync_request",
        )
        self.time_sync_echo_topic = os.environ.get(
            "MICROK3_TIME_SYNC_ECHO_TOPIC",
            "time_sync_echo",
        )
        self.status_topic = os.environ.get("MICROK3_STATUS_TOPIC", "microk3/node_status")
        self.alert_topic = os.environ.get("MICROK3_ALERT_TOPIC", "microk3/system_alerts")
        self.command_topic = os.environ.get("MICROK3_COMMAND_TOPIC", "microk3/commands")
        self.metrics_topic = os.environ.get("MICROK3_METRICS_TOPIC", "microk3/performance_metrics")
        self.timeout_sec = float(os.environ.get("RENODE_HEARTBEAT_TIMEOUT_SEC", "5.0"))
        self.publish_interval_sec = float(os.environ.get("MICROK3_METRICS_PUBLISH_SEC", "1.0"))

        self.status_pub = self.create_publisher(String, self.status_topic, 10)
        self.alert_pub = self.create_publisher(String, self.alert_topic, 10)
        self.metrics_pub = self.create_publisher(String, self.metrics_topic, 10)
        self.time_sync_request_pub = self.create_publisher(String, self.time_sync_request_topic, 10)
        heartbeat_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(Int32, self.heartbeat_topic, self.on_heartbeat, heartbeat_qos)
        self.create_subscription(String, self.heartbeat_telemetry_topic, self.on_heartbeat_telemetry, 10)
        if self.position_telemetry_topic:
            self.create_subscription(String, self.position_telemetry_topic, self.on_position_telemetry, 10)
        self.create_subscription(String, self.time_sync_echo_topic, self.on_time_sync_echo, 10)
        self.create_subscription(String, self.command_topic, self.on_command, 10)
        self.create_timer(1.0, self.on_timer)
        self.create_timer(self.publish_interval_sec, self.publish_metrics)
        self.create_timer(1.0, self.publish_time_sync_request)

        self.start_time = time.monotonic()
        self.last_heartbeat_time = None
        self.last_sequence = None
        self.offline_reported = False
        self.heartbeat_log_count = 0
        self.last_heartbeat_log_time = 0.0
        self.heartbeat_log_interval = float(os.environ.get("HEARTBEAT_LOG_INTERVAL_SEC", "30.0"))
        self.time_sync_sequence = 0
        self.time_sync_sent: Dict[int, float] = {}
        self.clock_offset_us: Optional[float] = None
        self.clock_scale: Optional[float] = None
        self.clock_intercept_us: Optional[float] = None
        self.time_sync_rtt_ms: Optional[float] = None
        self.time_sync_rtt_jitter_ms = 0.0
        self.time_sync_sample_count = 0
        self.last_time_sync_monotonic: Optional[float] = None
        self._time_sync_offset_samples: Deque[Dict[str, float]] = deque(maxlen=TopicTelemetryState.WINDOW_SIZE)
        self._heartbeat_receive_by_seq: Dict[int, float] = {}
        self._pending_telemetry_by_seq: Dict[int, Dict[str, object]] = {}
        self.topic_states = {
            "heartbeat": TopicTelemetryState("heartbeat"),
        }
        self._rtt_samples_ms: Deque[float] = deque(maxlen=TopicTelemetryState.WINDOW_SIZE)

        if self.position_telemetry_topic:
            self.topic_states["measured_position"] = TopicTelemetryState("measured_position")

        self.get_logger().info(
            f"Bridge online: heartbeat='{self.heartbeat_topic}', telemetry='{self.heartbeat_telemetry_topic}', "
            f"position='{self.position_telemetry_topic}', sync='{self.time_sync_request_topic}'/'{self.time_sync_echo_topic}' "
            f"-> '{self.metrics_topic}'"
        )

    def on_heartbeat(self, msg: Int32):
        self.last_heartbeat_time = time.monotonic()
        self.last_sequence = int(msg.data)
        sequence = int(msg.data)
        self._heartbeat_receive_by_seq[sequence] = self.last_heartbeat_time
        pending = self._pending_telemetry_by_seq.pop(sequence, None)
        if pending is not None:
            self._process_telemetry_payload(
                "heartbeat",
                pending["payload"],
                float(pending["receive_monotonic"]),
                int(pending["payload_bytes"]),
                self.last_heartbeat_time,
            )
        self._prune_cycle_tracking(sequence)
        was_offline = self.offline_reported
        self.offline_reported = False
        self.heartbeat_log_count += 1
        now = time.monotonic()
        should_log = self.heartbeat_log_interval > 0 and (
            self.heartbeat_log_count <= 3 or
            (now - self.last_heartbeat_log_time) >= self.heartbeat_log_interval
        )
        if should_log:
            self.last_heartbeat_log_time = now
            self.get_logger().info(f"Heartbeat received seq={self.last_sequence}")

        self.publish_status(status="active", health=100)

        if was_offline:
            self.publish_alert(level="info", message="Heartbeat resumed")

    def on_heartbeat_telemetry(self, msg: String):
        self._process_telemetry("heartbeat", msg, match_raw_heartbeat=True)

    def on_position_telemetry(self, msg: String):
        if "measured_position" not in self.topic_states:
            return
        self._process_telemetry("measured_position", msg)

    def _process_telemetry(self, logical_topic: str, msg: String, match_raw_heartbeat: bool = False):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warning(
                f"Ignoring invalid telemetry JSON on {logical_topic}: {msg.data}"
            )
            return

        receive_monotonic = time.monotonic()
        payload_bytes = len(msg.data.encode("utf-8"))
        if match_raw_heartbeat:
            sequence = int(payload.get("seq", -1))
            raw_receive_monotonic = self._heartbeat_receive_by_seq.pop(sequence, None)
            if raw_receive_monotonic is None:
                self._pending_telemetry_by_seq[sequence] = {
                    "payload": payload,
                    "receive_monotonic": receive_monotonic,
                    "payload_bytes": payload_bytes,
                }
                self._prune_cycle_tracking(sequence)
                return
            self._process_telemetry_payload(
                logical_topic,
                payload,
                receive_monotonic,
                payload_bytes,
                raw_receive_monotonic,
            )
            self._prune_cycle_tracking(sequence)
            return

        self._process_telemetry_payload(
            logical_topic,
            payload,
            receive_monotonic,
            payload_bytes,
            None,
        )

    def _process_telemetry_payload(
        self,
        logical_topic: str,
        payload: Dict[str, object],
        receive_monotonic: float,
        payload_bytes: int,
        raw_receive_monotonic: Optional[float],
    ) -> None:
        state = self.topic_states[logical_topic]
        state.update(
            payload,
            receive_monotonic,
            payload_bytes,
            self.clock_scale,
            self.clock_intercept_us,
            raw_receive_monotonic,
        )

    def _prune_cycle_tracking(self, latest_sequence: int) -> None:
        stale_cutoff = latest_sequence - (TopicTelemetryState.WINDOW_SIZE * 4)
        self._heartbeat_receive_by_seq = {
            seq: receive_time
            for seq, receive_time in self._heartbeat_receive_by_seq.items()
            if seq >= stale_cutoff
        }
        self._pending_telemetry_by_seq = {
            seq: pending
            for seq, pending in self._pending_telemetry_by_seq.items()
            if seq >= stale_cutoff
        }

    def publish_time_sync_request(self):
        host_send_us = time.monotonic_ns() // 1000
        payload = {
            "seq": self.time_sync_sequence,
            "host_send_us": host_send_us,
        }
        msg = String()
        msg.data = json.dumps(payload, separators=(",", ":"))
        self.time_sync_sent[self.time_sync_sequence] = float(host_send_us)
        stale_seq = self.time_sync_sequence - (TopicTelemetryState.WINDOW_SIZE * 4)
        self.time_sync_sent = {
            seq: sent_us
            for seq, sent_us in self.time_sync_sent.items()
            if seq >= stale_seq
        }
        self.time_sync_request_pub.publish(msg)
        self.time_sync_sequence += 1

    def on_time_sync_echo(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warning(f"Ignoring invalid time sync echo JSON: {msg.data}")
            return

        seq = int(payload.get("seq", -1))
        host_send_us = self.time_sync_sent.pop(seq, None)
        if host_send_us is None:
            return

        host_recv_us = float(time.monotonic_ns() // 1000)
        cm7_recv_us = float(payload.get("cm7_recv_us", 0.0))
        cm7_send_us = float(payload.get("cm7_send_us", 0.0))
        if cm7_recv_us <= 0.0 or cm7_send_us <= 0.0:
            return

        rtt_us = (host_recv_us - host_send_us) - max(0.0, cm7_send_us - cm7_recv_us)
        offset_us = ((host_send_us + host_recv_us) - (cm7_recv_us + cm7_send_us)) / 2.0
        host_mid_us = (host_send_us + host_recv_us) / 2.0
        cm7_mid_us = (cm7_recv_us + cm7_send_us) / 2.0

        candidate_rtt_ms = max(0.0, rtt_us) / 1000.0
        self._rtt_samples_ms.append(candidate_rtt_ms)
        self.time_sync_rtt_jitter_ms = (
            pstdev(self._rtt_samples_ms) if len(self._rtt_samples_ms) > 1 else 0.0
        )
        self.time_sync_rtt_ms = candidate_rtt_ms
        self._time_sync_offset_samples.append({
            "offset_us": offset_us,
            "host_mid_us": host_mid_us,
            "cm7_mid_us": cm7_mid_us,
            "rtt_ms": candidate_rtt_ms,
            "monotonic": time.monotonic(),
        })
        self._recompute_clock_mapping()
        self.time_sync_sample_count += 1
        self.last_time_sync_monotonic = time.monotonic()

    def _recompute_clock_mapping(self) -> None:
        if not self._time_sync_offset_samples:
            self.clock_scale = None
            self.clock_intercept_us = None
            self.clock_offset_us = None
            return

        recent_samples = list(self._time_sync_offset_samples)
        min_rtt_ms = min(sample["rtt_ms"] for sample in recent_samples)
        filtered_samples = [
            sample for sample in recent_samples
            if sample["rtt_ms"] <= max(min_rtt_ms * 1.5, min_rtt_ms + 10.0)
        ]
        if len(filtered_samples) < 2:
            filtered_samples = recent_samples

        latest_sample = recent_samples[-1]
        if len(filtered_samples) < 2:
            self.clock_scale = 1.0
            self.clock_intercept_us = latest_sample["host_mid_us"] - latest_sample["cm7_mid_us"]
            self.clock_offset_us = self.clock_intercept_us
            return

        mean_cm7 = sum(sample["cm7_mid_us"] for sample in filtered_samples) / len(filtered_samples)
        mean_host = sum(sample["host_mid_us"] for sample in filtered_samples) / len(filtered_samples)
        variance_cm7 = sum(
            (sample["cm7_mid_us"] - mean_cm7) ** 2 for sample in filtered_samples
        )
        if variance_cm7 <= 0.0:
            self.clock_scale = 1.0
            self.clock_intercept_us = latest_sample["host_mid_us"] - latest_sample["cm7_mid_us"]
            self.clock_offset_us = self.clock_intercept_us
            return

        covariance = sum(
            (sample["cm7_mid_us"] - mean_cm7) * (sample["host_mid_us"] - mean_host)
            for sample in filtered_samples
        )
        fitted_scale = covariance / variance_cm7
        if fitted_scale <= 0.0:
            fitted_scale = 1.0

        self.clock_scale = fitted_scale
        self.clock_intercept_us = mean_host - (self.clock_scale * mean_cm7)
        self.clock_offset_us = (
            (self.clock_scale * latest_sample["cm7_mid_us"]) +
            self.clock_intercept_us -
            latest_sample["cm7_mid_us"]
        )

    def on_command(self, msg: String):
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warning(f"Ignoring invalid command JSON: {msg.data}")
            return

        target_id = data.get("target_id")
        if target_id in (None, self.node_id, "all", "*"):
            self.get_logger().info(f"Observed dashboard command for node: {msg.data}")

    def on_timer(self):
        if self.last_heartbeat_time is None:
            return

        elapsed = time.monotonic() - self.last_heartbeat_time
        if elapsed < self.timeout_sec or self.offline_reported:
            return

        self.offline_reported = True
        self.publish_status(status="offline", health=0)
        self.publish_alert(
            level="warning",
            message=f"No heartbeat received for {elapsed:.1f}s"
        )

    def publish_status(self, status: str, health: int):
        now_monotonic = time.monotonic()
        heartbeat_metrics = self.topic_states["heartbeat"].as_dict(now_monotonic)
        sync_ready = self.is_sync_ready(now_monotonic)
        aggregate_bandwidth = sum(
            float(state.as_dict(now_monotonic).get("bandwidth_bps", 0.0))
            for state in self.topic_states.values()
        )
        payload = {
            "id": self.node_id,
            "name": self.node_name,
            "status": status,
            "health": health,
            "uptime": self.format_uptime(),
            "type": self.node_type,
            "network": self.node_network,
            "metrics_summary": {
                "client_to_agent_ms": heartbeat_metrics.get("client_to_agent_ms"),
                "client_to_agent_jitter_ms": heartbeat_metrics.get("client_to_agent_jitter_ms", 0.0),
                "agent_to_ros_ms": heartbeat_metrics.get("agent_to_ros_ms"),
                "agent_to_ros_jitter_ms": heartbeat_metrics.get("agent_to_ros_jitter_ms", 0.0),
                "end_to_end_ms": heartbeat_metrics.get("end_to_end_ms"),
                "end_to_end_jitter_ms": heartbeat_metrics.get("end_to_end_jitter_ms", 0.0),
                "lag_ms": heartbeat_metrics.get("lag_ms"),
                "jitter_ms": heartbeat_metrics.get("jitter_ms", 0.0),
                "sync_ready": sync_ready,
                "bandwidth_bps": round(aggregate_bandwidth, 3),
                "time_sync_rtt_ms": round(self.time_sync_rtt_ms, 3) if self.time_sync_rtt_ms is not None else None,
                "time_sync_rtt_jitter_ms": round(self.time_sync_rtt_jitter_ms, 3),
            },
        }
        if self.last_sequence is not None:
            payload["heartbeat_seq"] = self.last_sequence
            payload["heartbeat_raw"] = {
                "topic": self.heartbeat_topic,
                "type": "std_msgs/Int32",
                "data": self.last_sequence,
            }

        msg = String()
        msg.data = json.dumps(payload)
        self.status_pub.publish(msg)

    def publish_metrics(self):
        now_monotonic = time.monotonic()
        sync_ready = self.is_sync_ready(now_monotonic)
        topic_metrics = {
            logical_topic: state.as_dict(now_monotonic)
            for logical_topic, state in self.topic_states.items()
        }
        aggregate_bandwidth = sum(float(topic["bandwidth_bps"]) for topic in topic_metrics.values())
        aggregate_rate = sum(float(topic["rate_hz"]) for topic in topic_metrics.values())
        aggregate = {
            "client_to_agent_ms": topic_metrics["heartbeat"]["client_to_agent_ms"],
            "client_to_agent_jitter_ms": round(topic_metrics["heartbeat"]["client_to_agent_jitter_ms"], 3),
            "agent_to_ros_ms": topic_metrics["heartbeat"]["agent_to_ros_ms"],
            "agent_to_ros_jitter_ms": round(topic_metrics["heartbeat"]["agent_to_ros_jitter_ms"], 3),
            "end_to_end_ms": topic_metrics["heartbeat"]["end_to_end_ms"],
            "end_to_end_jitter_ms": round(topic_metrics["heartbeat"]["end_to_end_jitter_ms"], 3),
            "lag_ms": topic_metrics["heartbeat"]["lag_ms"],
            "jitter_ms": round(topic_metrics["heartbeat"]["jitter_ms"], 3),
            "raw_delta_ms": topic_metrics["heartbeat"]["raw_delta_ms"],
            "bandwidth_bps": round(aggregate_bandwidth, 3),
            "rate_hz": round(aggregate_rate, 3),
            "sync_ready": sync_ready,
            "clock_offset_ms": round(self.clock_offset_us / 1000.0, 3) if self.clock_offset_us is not None else None,
            "clock_scale": round(self.clock_scale, 9) if self.clock_scale is not None else None,
            "time_sync_rtt_ms": round(self.time_sync_rtt_ms, 3) if self.time_sync_rtt_ms is not None else None,
            "time_sync_rtt_jitter_ms": round(self.time_sync_rtt_jitter_ms, 3),
            "time_sync_samples": self.time_sync_sample_count,
        }

        payload = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "timestamp": utc_timestamp(),
            "aggregate": aggregate,
            "topics": topic_metrics,
        }
        msg = String()
        msg.data = json.dumps(payload)
        self.metrics_pub.publish(msg)

    def is_sync_ready(self, now_monotonic: Optional[float] = None) -> bool:
        if (
            self.clock_offset_us is None or
            self.clock_scale is None or
            self.clock_intercept_us is None or
            self.time_sync_sample_count <= 0
        ):
            return False
        if self.last_time_sync_monotonic is None:
            return False
        if now_monotonic is None:
            now_monotonic = time.monotonic()
        return (now_monotonic - self.last_time_sync_monotonic) <= max(5.0, self.publish_interval_sec * 3.0)

    def publish_alert(self, level: str, message: str):
        payload = {
            "node_id": self.node_id,
            "level": level,
            "msg": message,
        }
        msg = String()
        msg.data = json.dumps(payload)
        self.alert_pub.publish(msg)

    def format_uptime(self) -> str:
        total = int(time.monotonic() - self.start_time)
        hours = total // 3600
        minutes = (total % 3600) // 60
        seconds = total % 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"


def main():
    rclpy.init()
    node = RenodeHeartbeatBridge()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
