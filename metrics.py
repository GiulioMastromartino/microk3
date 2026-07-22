import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, Optional

try:
    import docker
except ImportError:  # pragma: no cover - optional at import time in some environments
    docker = None


METRIC_HISTORY_LIMIT = 60


def utc_timestamp() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def new_metrics_state() -> Dict[str, Any]:
    return {
        "last_update": None,
        "container_last_update": None,
        "aggregate": {
            "client_to_agent_ms": None,
            "client_to_agent_jitter_ms": 0.0,
            "agent_to_ros_ms": None,
            "agent_to_ros_jitter_ms": 0.0,
            "end_to_end_ms": None,
            "end_to_end_jitter_ms": 0.0,
            "lag_ms": None,
            "jitter_ms": 0.0,
            "raw_delta_ms": None,
            "bandwidth_bps": 0.0,
            "rate_hz": 0.0,
            "sync_ready": False,
            "clock_offset_ms": None,
            "clock_scale": None,
            "time_sync_rtt_ms": None,
            "time_sync_rtt_jitter_ms": 0.0,
            "time_sync_samples": 0,
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "memory_usage_bytes": 0,
            "rx_bps": 0.0,
            "tx_bps": 0.0,
        },
        "node_metrics": {},
        "topic_metrics": {},
        "container_resources": {},
        "history": {
            "timestamps": [],
            "client_to_agent_ms": [],
            "client_to_agent_jitter_ms": [],
            "agent_to_ros_ms": [],
            "agent_to_ros_jitter_ms": [],
            "end_to_end_ms": [],
            "end_to_end_jitter_ms": [],
            "lag_ms": [],
            "jitter_ms": [],
            "bandwidth_bps": [],
            "cpu_percent": [],
            "memory_percent": [],
            "rx_bps": [],
            "tx_bps": [],
        },
    }


def _append_history(history: Dict[str, Any], **values: Any) -> None:
    history["timestamps"].append(values.pop("timestamp", utc_timestamp()))
    for key, value in values.items():
        history.setdefault(key, []).append(value)

    for key, series in history.items():
        if len(series) > METRIC_HISTORY_LIMIT:
            del series[:-METRIC_HISTORY_LIMIT]


def merge_performance_metrics(metrics_state: Dict[str, Any], payload: Dict[str, Any]) -> None:
    node_id = str(payload.get("node_id", "unknown"))
    timestamp = payload.get("timestamp", utc_timestamp())
    aggregate = payload.get("aggregate", {})
    topics = payload.get("topics", {})

    metrics_state["last_update"] = timestamp
    metrics_state["node_metrics"][node_id] = payload
    metrics_state["topic_metrics"] = topics
    metrics_state["aggregate"].update({
        "client_to_agent_ms": _optional_float(aggregate.get("client_to_agent_ms")),
        "client_to_agent_jitter_ms": float(aggregate.get("client_to_agent_jitter_ms", 0.0)),
        "agent_to_ros_ms": _optional_float(aggregate.get("agent_to_ros_ms")),
        "agent_to_ros_jitter_ms": float(aggregate.get("agent_to_ros_jitter_ms", 0.0)),
        "end_to_end_ms": _optional_float(aggregate.get("end_to_end_ms")),
        "end_to_end_jitter_ms": float(aggregate.get("end_to_end_jitter_ms", 0.0)),
        "lag_ms": _optional_float(aggregate.get("lag_ms")),
        "jitter_ms": float(aggregate.get("jitter_ms", 0.0)),
        "raw_delta_ms": _optional_float(aggregate.get("raw_delta_ms")),
        "bandwidth_bps": float(aggregate.get("bandwidth_bps", 0.0)),
        "rate_hz": float(aggregate.get("rate_hz", 0.0)),
        "sync_ready": bool(aggregate.get("sync_ready", False)),
        "clock_offset_ms": aggregate.get("clock_offset_ms"),
        "clock_scale": _optional_float(aggregate.get("clock_scale")),
        "time_sync_rtt_ms": aggregate.get("time_sync_rtt_ms"),
        "time_sync_rtt_jitter_ms": float(aggregate.get("time_sync_rtt_jitter_ms", 0.0)),
        "time_sync_samples": int(aggregate.get("time_sync_samples", 0)),
    })
    _append_history(
        metrics_state["history"],
        timestamp=timestamp,
        client_to_agent_ms=metrics_state["aggregate"]["client_to_agent_ms"],
        client_to_agent_jitter_ms=metrics_state["aggregate"]["client_to_agent_jitter_ms"],
        agent_to_ros_ms=metrics_state["aggregate"]["agent_to_ros_ms"],
        agent_to_ros_jitter_ms=metrics_state["aggregate"]["agent_to_ros_jitter_ms"],
        end_to_end_ms=metrics_state["aggregate"]["end_to_end_ms"],
        end_to_end_jitter_ms=metrics_state["aggregate"]["end_to_end_jitter_ms"],
        lag_ms=metrics_state["aggregate"]["lag_ms"],
        jitter_ms=metrics_state["aggregate"]["jitter_ms"],
        bandwidth_bps=metrics_state["aggregate"]["bandwidth_bps"],
        cpu_percent=metrics_state["aggregate"]["cpu_percent"],
        memory_percent=metrics_state["aggregate"]["memory_percent"],
        rx_bps=metrics_state["aggregate"]["rx_bps"],
        tx_bps=metrics_state["aggregate"]["tx_bps"],
    )


def merge_container_metrics(metrics_state: Dict[str, Any], payload: Dict[str, Any]) -> None:
    timestamp = payload.get("timestamp", utc_timestamp())
    containers = payload.get("containers", {})
    aggregate = payload.get("aggregate", {})

    metrics_state["container_last_update"] = timestamp
    metrics_state["container_resources"] = containers
    metrics_state["aggregate"].update({
        "cpu_percent": float(aggregate.get("cpu_percent", 0.0)),
        "memory_percent": float(aggregate.get("memory_percent", 0.0)),
        "memory_usage_bytes": int(aggregate.get("memory_usage_bytes", 0)),
        "rx_bps": float(aggregate.get("rx_bps", 0.0)),
        "tx_bps": float(aggregate.get("tx_bps", 0.0)),
    })
    _append_history(
        metrics_state["history"],
        timestamp=timestamp,
        client_to_agent_ms=metrics_state["aggregate"]["client_to_agent_ms"],
        client_to_agent_jitter_ms=metrics_state["aggregate"]["client_to_agent_jitter_ms"],
        agent_to_ros_ms=metrics_state["aggregate"]["agent_to_ros_ms"],
        agent_to_ros_jitter_ms=metrics_state["aggregate"]["agent_to_ros_jitter_ms"],
        end_to_end_ms=metrics_state["aggregate"]["end_to_end_ms"],
        end_to_end_jitter_ms=metrics_state["aggregate"]["end_to_end_jitter_ms"],
        lag_ms=metrics_state["aggregate"]["lag_ms"],
        jitter_ms=metrics_state["aggregate"]["jitter_ms"],
        bandwidth_bps=metrics_state["aggregate"]["bandwidth_bps"],
        cpu_percent=metrics_state["aggregate"]["cpu_percent"],
        memory_percent=metrics_state["aggregate"]["memory_percent"],
        rx_bps=metrics_state["aggregate"]["rx_bps"],
        tx_bps=metrics_state["aggregate"]["tx_bps"],
    )


def build_metrics_summary(metrics_state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "last_update": metrics_state.get("last_update"),
        "container_last_update": metrics_state.get("container_last_update"),
        "aggregate": dict(metrics_state.get("aggregate", {})),
        "topics": dict(metrics_state.get("topic_metrics", {})),
        "containers": dict(metrics_state.get("container_resources", {})),
        "history": {key: list(values) for key, values in metrics_state.get("history", {}).items()},
        "nodes": dict(metrics_state.get("node_metrics", {})),
    }


def _cpu_percent_from_stats(stats: Dict[str, Any]) -> float:
    cpu_stats = stats.get("cpu_stats", {})
    precpu_stats = stats.get("precpu_stats", {})
    current_total = cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
    previous_total = precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
    current_system = cpu_stats.get("system_cpu_usage", 0)
    previous_system = precpu_stats.get("system_cpu_usage", 0)
    online_cpus = cpu_stats.get("online_cpus") or len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", []) or [1])

    cpu_delta = current_total - previous_total
    system_delta = current_system - previous_system
    if cpu_delta <= 0 or system_delta <= 0 or online_cpus <= 0:
        return 0.0
    return (cpu_delta / system_delta) * online_cpus * 100.0


def _memory_usage_from_stats(stats: Dict[str, Any]) -> Dict[str, float]:
    memory_stats = stats.get("memory_stats", {})
    usage = float(memory_stats.get("usage", 0.0))
    limit = float(memory_stats.get("limit", 0.0))
    percent = (usage / limit * 100.0) if usage > 0.0 and limit > 0.0 else 0.0
    return {
        "usage_bytes": usage,
        "limit_bytes": limit,
        "percent": percent,
    }


def _network_counters_from_stats(stats: Dict[str, Any]) -> Dict[str, float]:
    rx_bytes = 0.0
    tx_bytes = 0.0
    for network_stats in (stats.get("networks") or {}).values():
        rx_bytes += float(network_stats.get("rx_bytes", 0.0))
        tx_bytes += float(network_stats.get("tx_bytes", 0.0))
    return {
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
    }


class DockerStatsCollector(threading.Thread):
    def __init__(self, update_callback: Callable[[Dict[str, Any]], None], logger) -> None:
        super().__init__(daemon=True, name="DockerStatsCollector")
        self.update_callback = update_callback
        self.logger = logger
        self.interval_sec = float(os.environ.get("MICROK3_METRICS_INTERVAL_SEC", "2.0"))
        self.service_names = [
            service.strip()
            for service in os.environ.get(
                "MICROK3_METRICS_DOCKER_SERVICES",
                "micro-ros-agent,microk3,renode-bridge",
            ).split(",")
            if service.strip()
        ]
        self._stop_event = threading.Event()
        self._previous_counters: Dict[str, Dict[str, float]] = {}
        self._client = None

    def available(self) -> bool:
        return docker is not None and os.path.exists("/var/run/docker.sock") and bool(self.service_names)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # pragma: no cover - runtime integration path
        if not self.available():
            self.logger.info("Docker stats collector disabled: docker SDK/socket unavailable or no services configured")
            return

        try:
            self._client = docker.from_env()
            self._client.ping()
        except Exception as exc:
            self.logger.warning("Docker stats collector unavailable: %s", exc)
            return

        self.logger.info("Docker stats collector started for services=%s", ",".join(self.service_names))
        while not self._stop_event.is_set():
            try:
                payload = self.collect_once()
                if payload:
                    self.update_callback(payload)
            except Exception as exc:
                self.logger.warning("Docker stats collection failed: %s", exc)
            self._stop_event.wait(self.interval_sec)

    def collect_once(self) -> Dict[str, Any]:
        assert self._client is not None

        now_monotonic = time.monotonic()
        timestamp = utc_timestamp()
        containers: Dict[str, Dict[str, Any]] = {}
        totals = {
            "cpu_percent": 0.0,
            "memory_usage_bytes": 0.0,
            "memory_limit_bytes": 0.0,
            "rx_bps": 0.0,
            "tx_bps": 0.0,
        }

        for container in self._client.containers.list():
            labels = container.labels or {}
            service_name = labels.get("com.docker.compose.service")
            if service_name not in self.service_names:
                continue

            stats = container.stats(stream=False)
            cpu_percent = _cpu_percent_from_stats(stats)
            memory = _memory_usage_from_stats(stats)
            counters = _network_counters_from_stats(stats)
            previous = self._previous_counters.get(service_name)
            rx_bps = 0.0
            tx_bps = 0.0
            if previous is not None:
                elapsed = max(now_monotonic - previous["timestamp"], 1e-6)
                rx_bps = max(0.0, (counters["rx_bytes"] - previous["rx_bytes"]) / elapsed)
                tx_bps = max(0.0, (counters["tx_bytes"] - previous["tx_bytes"]) / elapsed)

            self._previous_counters[service_name] = {
                "timestamp": now_monotonic,
                "rx_bytes": counters["rx_bytes"],
                "tx_bytes": counters["tx_bytes"],
            }

            service_payload = {
                "service": service_name,
                "container_name": container.name,
                "status": container.status,
                "cpu_percent": round(cpu_percent, 3),
                "memory_usage_bytes": int(memory["usage_bytes"]),
                "memory_limit_bytes": int(memory["limit_bytes"]),
                "memory_percent": round(memory["percent"], 3),
                "rx_bps": round(rx_bps, 3),
                "tx_bps": round(tx_bps, 3),
            }
            containers[service_name] = service_payload
            totals["cpu_percent"] += cpu_percent
            totals["memory_usage_bytes"] += memory["usage_bytes"]
            totals["memory_limit_bytes"] += memory["limit_bytes"]
            totals["rx_bps"] += rx_bps
            totals["tx_bps"] += tx_bps

        memory_percent = (
            totals["memory_usage_bytes"] / totals["memory_limit_bytes"] * 100.0
            if totals["memory_usage_bytes"] > 0.0 and totals["memory_limit_bytes"] > 0.0
            else 0.0
        )

        return {
            "timestamp": timestamp,
            "containers": containers,
            "aggregate": {
                "cpu_percent": round(totals["cpu_percent"], 3),
                "memory_usage_bytes": int(totals["memory_usage_bytes"]),
                "memory_percent": round(memory_percent, 3),
                "rx_bps": round(totals["rx_bps"], 3),
                "tx_bps": round(totals["tx_bps"], 3),
            },
        }
