"""Jetson host diagnostics collector for Orin NX.

Gathers temperatures (thermal zones), per-core CPU utilization via
/proc/stat deltas, per-core CPU frequencies via cpufreq sysfs,
GPU utilization/frequency and power via jetson-stats (optional),
and memory via /proc/meminfo.

Runs as a daemon thread, pushing payloads through the same
ros_update_callback mechanism used by DockerStatsCollector.

On non-Jetson hosts (e.g. macOS) it disables itself gracefully.
"""

import glob
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# jetson-stats is optional — sysfs path still works without it.
# jetson-stats 7.x top-level import is `jtop`, not `jetson`
try:
    from jtop import jtop as JTOP_CLASS  # type: ignore
except Exception:  # pragma: no cover - missing in dev/macos
    JTOP_CLASS = None  # type: ignore  # type: ignore
    try:
        from jetson import jtop as JTOP_CLASS  # type: ignore  # legacy jetson-stats <5
    except Exception:  # pragma: no cover
        JTOP_CLASS = None  # type: ignore


THERMAL_TYPE_GLOB = "/sys/class/thermal/thermal_zone*/type"
THERMAL_TEMP_RE = re.compile(r"thermal_zone(\d+)")


def utc_timestamp() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _read_thermal_zones() -> Dict[str, Any]:
    temps: Dict[str, Any] = {}
    for type_path in glob.glob(THERMAL_TYPE_GLOB):
        try:
            zone_dir = os.path.dirname(type_path)
            with open(type_path) as f:
                zone_type = f.read().strip()
            temp_path = os.path.join(zone_dir, "temp")
            with open(temp_path) as f:
                raw = int(f.read().strip())
            c = raw / 1000.0
            temps[zone_type] = round(c, 2)
        except Exception:
            continue
    return temps


def _read_cpu_freqs() -> Dict[str, Optional[float]]:
    freqs: Dict[str, Optional[float]] = {}
    for path in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")):
        m = re.search(r"cpu(\d+)", path)
        if not m:
            continue
        idx = m.group(1)
        try:
            with open(path) as f:
                khz = int(f.read().strip())
            freqs[f"cpu{idx}"] = round(khz / 1000.0, 1)  # MHz
        except Exception:
            freqs[f"cpu{idx}"] = None
    return freqs


def _read_gpu_util() -> Optional[float]:
    for p in ("/sys/devices/platform/17000000.gpu/load", "/sys/devices/gpu.0/load"):
        try:
            with open(p) as f:
                v = int(f.read().strip())
            return round(float(v), 1)
        except Exception:
            continue
    return None


def _read_power() -> Dict[str, Any]:
    """Read INA3221 hwmon (VDD_IN / VDD_CPU_GPU_CV / VDD_SOC) inside container."""
    try:
        base = "/sys/class/hwmon/hwmon2"
        rails: Dict[str, Any] = {}
        total = None
        for i in (1, 2, 3):
            label_p = os.path.join(base, f"in{i}_label")
            vin_p = os.path.join(base, f"in{i}_input")
            curr_p = os.path.join(base, f"curr{i}_input")
            try:
                with open(label_p) as f:
                    label = f.read().strip()
                with open(vin_p) as f:
                    vin = int(f.read().strip())  # mV
                with open(curr_p) as f:
                    curr = int(f.read().strip())  # mA
                w = round(vin * curr / 1_000_000.0, 2)
                rails[label] = w
                if label == "VDD_IN":
                    total = w
            except Exception:
                continue
        # fallback: sum rails if total missing
        if total is None and rails:
            total = round(sum(rails.values()), 2)
        return {"total_w": total, "rails": rails}
    except Exception:
        return {"total_w": None, "rails": {}}


def _read_meminfo() -> Dict[str, Any]:
    try:
        with open("/proc/meminfo") as f:
            lines = dict(
                (parts[0].rstrip(":"), parts[1].strip())
                for parts in (l.split(":", 1) for l in f if ":" in l)
            )
        total_kb = int(lines.get("MemTotal", "0").split()[0])
        avail_kb = int(lines.get("MemAvailable", "0").split()[0])
        used_kb = max(total_kb - avail_kb, 0)
        percent = (used_kb / total_kb * 100.0) if total_kb else 0.0
        return {
            "total_mb": round(total_kb / 1024.0, 1),
            "used_mb": round(used_kb / 1024.0, 1),
            "percent": round(percent, 1),
        }
    except Exception:
        return {"total_mb": None, "used_mb": None, "percent": None}


def _parse_proc_stat() -> Dict[str, List[int]]:
    out: Dict[str, List[int]] = {}
    try:
        with open("/proc/stat") as f:
            for line in f:
                if not line.startswith("cpu"):
                    continue
                parts = line.split()
                name = parts[0]
                vals = [int(x) for x in parts[1:]]
                out[name] = vals
    except Exception:
        pass
    return out


def _cpu_util_from_delta(prev: Dict[str, List[int]], cur: Dict[str, List[int]]) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {}
    for name, cur_vals in cur.items():
        prev_vals = prev.get(name)
        if prev_vals is None or len(cur_vals) < 4 or len(prev_vals) < 4:
            result[name] = None
            continue
        prev_idle = prev_vals[3] + (prev_vals[4] if len(prev_vals) > 4 else 0)
        cur_idle = cur_vals[3] + (cur_vals[4] if len(cur_vals) > 4 else 0)
        prev_total = sum(prev_vals)
        cur_total = sum(cur_vals)
        total_d = cur_total - prev_total
        idle_d = cur_idle - prev_idle
        if total_d <= 0:
            result[name] = 0.0
        else:
            result[name] = round((total_d - idle_d) / total_d * 100.0, 1)
    return result


class JetsonDiagnosticsCollector(threading.Thread):
    def __init__(self, update_callback: Callable[[Dict[str, Any]], None], logger) -> None:
        super().__init__(daemon=True, name="JetsonDiagnosticsCollector")
        self.update_callback = update_callback
        self.logger = logger
        self.interval_sec = float(os.environ.get("JETSON_DIAG_INTERVAL_SEC", "2.0"))
        self._stop_event = threading.Event()
        self._prev_stat: Optional[Dict[str, List[int]]] = None
        # board model
        self.board_model = self._read_board_model()

    @staticmethod
    def _read_board_model() -> str:
        # /proc/device-tree is symlink to /sys/firmware/devicetree/base on Jetson;
        # inside container it may be missing if not mounted, try both.
        for p in (
            "/proc/device-tree/model",
            "/sys/firmware/devicetree/base/model",
        ):
            try:
                with open(p, "rb") as f:
                    raw = f.read().strip(b"\x00")
                val = raw.decode(errors="ignore").strip()
                if val and val not in ("unknown", "generic"):
                    return val
            except Exception:
                continue
        # compatible is binary NUL-separated, e.g. nvidia,p3768-0000\x00nvidia,tegra234\x00
        for p in (
            "/proc/device-tree/compatible",
            "/sys/firmware/devicetree/base/compatible",
        ):
            try:
                with open(p, "rb") as f:
                    raw = f.read()
                # split on NUL, take first entry
                first = raw.split(b"\x00")[0].decode(errors="ignore").strip()
                if first and first not in ("unknown", "generic"):
                    return first
            except Exception:
                continue
        # fallback: nv_tegra_release (skip generic)
        try:
            with open("/etc/nv_tegra_release") as f:
                txt = f.read()
                m = re.search(r"BOARD:\s*([^\n,]+)", txt)
                if m:
                    b = m.group(1).strip()
                    if b and b.lower() != "generic":
                        return b
        except Exception:
            pass
        # fallback: check if thermal zones indicate Jetson
        # container cannot access /sys/firmware/devicetree (virtual fs not bind-mountable),
        # so return generic Jetson name when thermal zones prove it's a Jetson.
        if glob.glob(THERMAL_TYPE_GLOB):
            return "NVIDIA Jetson Orin NX"
        return "unknown"

    def available(self) -> bool:
        flag = os.environ.get("JETSON_DIAGNOSTICS_ENABLED", "auto").strip().lower()
        if flag in ("0", "false", "off", "no"):
            return False
        if flag in ("1", "true", "on", "yes"):
            return True
        # auto-detect: Jetson thermal zones present
        zones = glob.glob(THERMAL_TYPE_GLOB)
        if zones:
            return True
        if JTOP_CLASS is not None:
            return True
        return False

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # pragma: no cover - runtime integration path
        if not self.available():
            self.logger.info("Jetson diagnostics disabled: not running on Jetson hardware")
            try:
                self.update_callback(
                    {"timestamp": utc_timestamp(), "available": False, "reason": "not a Jetson host"}
                )
            except Exception:
                pass
            return

        # jtop/daemon is optional: sysfs fallback (GPU via /sys/devices/platform/*/load, power via INA hwmon) covers everything needed.
        # Try jtop once; if it fails, use sysfs-only silently.
        try:
            jetson = JTOP_CLASS()  # type: ignore
        except Exception:
            jetson = None

        if jetson is not None:
            self.logger.info("Jetson diagnostics: jtop available (jetson-stats)")
            try:
                with jetson:  # type: ignore
                    self._prev_stat = _parse_proc_stat()
                    time.sleep(0.2)
                    while not self._stop_event.is_set():
                        try:
                            payload = self._collect_via_jetson(jetson)
                            if payload:
                                self.update_callback(payload)
                        except Exception as exc:
                            self.logger.debug("Jetson jtop collection failed, falling back: %s", exc)
                            payload = self._collect_via_sysfs()
                            if payload:
                                self.update_callback(payload)
                        self._stop_event.wait(self.interval_sec)
                return
            except Exception as exc:
                self.logger.debug("jtop not usable, using sysfs: %s", exc)

        self.logger.info("Jetson diagnostics: sysfs mode (temps/CPU/freq/mem, GPU/power via sysfs)")
        self._prev_stat = _parse_proc_stat()
        while not self._stop_event.is_set():
            try:
                payload = self._collect_via_sysfs()
                if payload:
                    self.update_callback(payload)
            except Exception as exc:
                self.logger.warning("Jetson sysfs collection failed: %s", exc)
            self._stop_event.wait(self.interval_sec)

    def _collect_via_sysfs(self) -> Dict[str, Any]:
        temps = _read_thermal_zones()
        freqs = _read_cpu_freqs()
        mem = _read_meminfo()
        cur_stat = _parse_proc_stat()
        util = None
        if self._prev_stat is not None and cur_stat:
            util = _cpu_util_from_delta(self._prev_stat, cur_stat)
        self._prev_stat = cur_stat

        # Normalise util into aggregate + per-core
        aggregate = None
        cores: List[Dict[str, Any]] = []
        if util:
            aggregate = util.get("cpu")
            for path in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*")):
                m = re.search(r"cpu(\d+)", path)
                if not m:
                    continue
                idx = m.group(1)
                key = f"cpu{idx}"
                cores.append(
                    {
                        "core": int(idx),
                        "percent": util.get(key),
                        "freq_mhz": freqs.get(key),
                    }
                )
        else:
            for key, f in freqs.items():
                m = re.search(r"(\d+)", key)
                cores.append({"core": int(m.group(1)) if m else 0, "percent": None, "freq_mhz": f})

        gpu_pct = _read_gpu_util()
        power = _read_power()
        return {
            "timestamp": utc_timestamp(),
            "available": True,
            "board": self.board_model,
            "source": "sysfs",
            "temperatures": temps,
            "power": power,
            "cpu": {"aggregate_percent": aggregate, "cores": cores},
            "gpu": {"percent": gpu_pct, "freq_mhz": None},
            "memory": mem,
        }

    def _collect_via_jetson(self, jetson) -> Dict[str, Any]:
        # Base sysfs temps/freqs as fallback for fields jtop may miss
        sys_temps = _read_thermal_zones()
        sys_freqs = _read_cpu_freqs()
        sys_mem = _read_meminfo()

        stats = {}
        try:
            stats = jetson.stats  # dict-like
        except Exception as exc:
            self.logger.debug("jetson.stats unavailable: %s", exc)
            stats = {}

        # Temperatures
        try:
            j_temps = dict(stats.get("temperature") or {})  # type: ignore
            # jetson-stats gives dict like {'CPU': 42.1, 'GPU': 38.2, ...}
            merged_temps = dict(sys_temps)
            for k, v in j_temps.items():
                # ensure key normalised, keep numeric
                try:
                    merged_temps[k] = round(float(v), 2) if v is not None else None
                except Exception:
                    merged_temps[k] = v
            temps = merged_temps
        except Exception:
            temps = sys_temps

        # Power — jetson-stats: {'total': {'power': 12345}, 'VDD_CPU_GPU_CV': {'power':...}, }
        power_total_w: Optional[float] = None
        power_rails: Dict[str, Any] = {}
        try:
            p = stats.get("power") or {}  # type: ignore
            # total is p.get('total') or p.get('tot') depending on version
            total = p.get("total") or p.get("tot")
            if isinstance(total, dict) and "power" in total:
                val = total["power"]
                power_total_w = round(float(val) / 1000.0, 2) if val is not None else None
            for k, v in p.items():
                if isinstance(v, dict) and "power" in v:
                    try:
                        power_rails[k] = round(float(v["power"]) / 1000.0, 2)
                    except Exception:
                        power_rails[k] = None
        except Exception:
            pass
        # sysfs fallback for power if jtop didn't provide it (GPU/power via hwmon inside container)
        if power_total_w is None:
            try:
                p2 = _read_power()
                if p2.get("total_w") is not None:
                    if not power_rails:
                        power_rails = p2.get("rails", {})
                    if power_total_w is None:
                        power_total_w = p2.get("total_w")
            except Exception:
                pass

        # CPU util via proc delta (jetson-stats cpu is also available but proc is reliable)
        cur_stat = _parse_proc_stat()
        util = None
        if self._prev_stat is not None and cur_stat:
            util = _cpu_util_from_delta(self._prev_stat, cur_stat)
        self._prev_stat = cur_stat

        # Enrich CPU cores with jtop frequencies when present
        # jtop stats['frequency'] is like {'CPU': [{'freq': 1984}, ...], 'GPU': {'freq': 1305}}
        freq_map: Dict[str, Any] = {}
        try:
            freq_dict = dict(stats.get("frequency") or {})  # type: ignore
            for k, v in freq_dict.items():
                freq_map[k] = v
        except Exception:
            pass

        cores: List[Dict[str, Any]] = []
        if util:
            aggregate = util.get("cpu")
            for path in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*")):
                m = re.search(r"cpu(\d+)", path)
                if not m:
                    continue
                idx = m.group(1)
                key = f"cpu{idx}"
                f = sys_freqs.get(key)
                # try jtop frequency override for this core/cluster
                try:
                    # jtop uses clusters: frequency['CPU'] is list of per-cluster freqs
                    # For Orin NX (8 cores, clusters), map via simple heuristic: use sysfs value
                    pass
                except Exception:
                    pass
                cores.append(
                    {"core": int(idx), "percent": util.get(key), "freq_mhz": f}
                )
            # aggregate above
        else:
            aggregate = None
            for key, f in sys_freqs.items():
                m = re.search(r"(\d+)", key)
                cores.append({"core": int(m.group(1)) if m else 0, "percent": None, "freq_mhz": f})
            # recompute aggregate for return (None)

        # GPU util/freq
        gpu_percent: Optional[float] = None
        gpu_freq: Optional[float] = None
        try:
            g = stats.get("gpu") or stats.get("GPU")  # type: ignore
            if isinstance(g, dict):
                gpu_percent = g.get("val") or g.get("percent") or g.get("util")
                if gpu_percent is not None:
                    gpu_percent = round(float(gpu_percent), 1)
                freq_mhz = g.get("freq") or g.get("frq")
                if freq_mhz is not None:
                    gpu_freq = round(float(freq_mhz), 1)
            elif g is not None:
                gpu_percent = round(float(g), 1)
        except Exception:
            pass
        # Fallback: frequency dict may have GPU
        if gpu_freq is None:
            try:
                gf = freq_map.get("GPU") or freq_map.get("gpu")
                if isinstance(gf, dict):
                    v = gf.get("freq") or gf.get("val")
                    if v is not None:
                        gpu_freq = round(float(v), 1)
                elif isinstance(gf, (list, tuple)) and gf:
                    v = gf[0]
                    if isinstance(v, dict):
                        gpu_freq = round(float(v.get("freq", 0) or 0), 1)
            except Exception:
                pass

        # sysfs fallback for GPU util (% via /sys/devices/platform/17000000.gpu/load)
        if gpu_percent is None:
            try:
                gp = _read_gpu_util()
                if gp is not None:
                    gpu_percent = gp
            except Exception:
                pass

        # Memory — prefer jetson-stats 'mem' or sys
        mem = sys_mem
        try:
            m = stats.get("mem") or stats.get("RAM")  # type: ignore
            if isinstance(m, dict):
                used = m.get("used") or m.get("RAM")
                total = m.get("total") or m.get("TOT")
                percent = m.get("percent")
                if used is not None and total is not None:
                    mem = {
                        "used_mb": round(float(used) / 1024.0, 1) if float(total) > 0 else None,
                        "total_mb": round(float(total) / 1024.0, 1),
                        "percent": round(float(percent), 1) if percent is not None else None,
                    }
        except Exception:
            pass

        return {
            "timestamp": utc_timestamp(),
            "available": True,
            "board": self.board_model,
            "source": "jetson-stats" if stats else "sysfs",
            "temperatures": temps,
            "power": {"total_w": power_total_w, "rails": power_rails},
            "cpu": {"aggregate_percent": aggregate if 'aggregate' in locals() else None, "cores": cores},
            "gpu": {"percent": gpu_percent, "freq_mhz": gpu_freq},
            "memory": mem,
        }
