# -*- coding: utf-8 -*-
"""موارد الخادم: CPU/RAM/قرص من /proc مباشرة — بلا مكتبات خارجية.

update_state(s): يكتب state['resources'] = {cpu_pct, ram_pct, disk_pct,
load1, cores, ts} — يقرأها الداشبورد (لوحة 'موارد الخادم').
fail-safe: أي فشل → تُتخطى بصمت.
"""
import os
import time


def _cpu_pct():
    """نسبة استعمال CPU منذ آخر استدعاء (أو 0.5s عينة أول مرة)."""
    try:
        def snap():
            with open("/proc/stat") as f:
                p = f.readline().split()
            vals = list(map(int, p[1:8]))
            idle = vals[3] + vals[4]
            return sum(vals), idle
        t0, i0 = snap()
        time.sleep(0.5)
        t1, i1 = snap()
        dt, di = t1 - t0, i1 - i0
        if dt <= 0:
            return 0.0
        return round(max(0.0, min(100.0, (1 - di / dt) * 100)), 1)
    except Exception:
        return None


def _mem():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = int(v.split()[0])
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        if not total:
            return None, None
        used_pct = round((1 - avail / total) * 100, 1)
        return used_pct, round(total / 1024 / 1024, 1)  # GB
    except Exception:
        return None, None


def _disk(path="/"):
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        if not total:
            return None
        return round((1 - free / total) * 100, 1)
    except Exception:
        return None


def _load():
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[0])
    except Exception:
        return None


def collect():
    """يجمع الموارد — يعيد قاموساً (قيم None عند التعذر)."""
    try:
        ram_pct, ram_gb = _mem()
        return {
            "cpu_pct": _cpu_pct(),
            "ram_pct": ram_pct,
            "ram_gb": ram_gb,
            "disk_pct": _disk(),
            "load1": _load(),
            "cores": os.cpu_count() or 0,
            "ts": time.time(),
        }
    except Exception:
        return {}


def update_state(s):
    """يكتب state['resources'] — آمن للاستدعاء من المراقب كل دقيقة."""
    try:
        d = collect()
        if d:
            s["resources"] = d
            return True
        return False
    except Exception as e:
        print(f"[resources] skipped: {e}")
        return False
