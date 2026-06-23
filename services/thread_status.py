"""后台守护线程状态注册中心：各线程上报心跳，供前端展示运行状态。"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

_lock = threading.Lock()
# {name: {last_heartbeat, interval_seconds, last_run_at, message}}
_threads: dict[str, dict[str, Any]] = {}


def register(name: str, interval_seconds: int) -> None:
    """线程启动时注册自己。"""
    with _lock:
        _threads[name] = {
            "last_heartbeat": time.time(),
            "interval_seconds": max(1, int(interval_seconds)),
            "last_run_at": "",
            "message": "",
        }


def heartbeat(name: str, message: str = "") -> None:
    """线程每轮循环上报心跳。"""
    now = time.time()
    with _lock:
        info = _threads.get(name)
        if info is None:
            info = {"last_heartbeat": now, "interval_seconds": 0, "last_run_at": "", "message": ""}
            _threads[name] = info
        info["last_heartbeat"] = now
        info["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if message:
            info["message"] = message


def snapshot() -> list[dict[str, Any]]:
    """返回所有线程状态快照。"""
    now = time.time()
    with _lock:
        result = []
        for name, info in _threads.items():
            interval = info.get("interval_seconds", 60)
            # 阈值随线程间隔放大（×3），不 clamp 上限：image-cleanup 30min 间隔，
            # clamp 到 300s 会导致它永远被判为"未活跃"
            threshold = max(interval * 3, 120)
            age = now - info.get("last_heartbeat", 0)
            result.append({
                "name": name,
                "alive": age < threshold,
                "interval_seconds": interval,
                "last_run_at": info.get("last_run_at", ""),
                "message": info.get("message", ""),
                "idle_seconds": round(age, 0),
            })
        return result


class _ThreadStatus:
    """单例：以实例方法风格暴露模块函数，供各后台线程调用。"""

    register = staticmethod(register)
    heartbeat = staticmethod(heartbeat)
    snapshot = staticmethod(snapshot)


thread_status = _ThreadStatus()
