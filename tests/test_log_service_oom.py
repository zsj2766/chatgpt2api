"""日志服务验证测试。

历史 bug：list()/delete()/_maybe_trim 全量 read_text 致 OOM，logs.jsonl 累积 7.8GB。
根治方案：日志走 stderr 不落盘（docker logs 采集），移除日志页/端点/文件 I/O。
本测试验证：add() 输出 stderr 且不创建 logs.jsonl 文件、单行体积截断、线程安全。
纯标准库，可直接 `python tests/test_log_service_oom.py` 运行。
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.log_service import LogService, LOG_TYPE_REGISTER, LOG_TYPE_CALL


def test_add_outputs_stderr_no_file(tmp: Path) -> None:
    """add() 走 stderr，不创建任何 jsonl 文件。"""
    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    logger = logging.getLogger("chatgpt2api.log")
    logger.addHandler(handler)
    try:
        svc = LogService()
        svc.add(LOG_TYPE_CALL, "测试调用", {"k": "v"})
        out = captured.getvalue()
        item = json.loads(out.strip())
        assert item["summary"] == "测试调用"
        assert item["type"] == LOG_TYPE_CALL
        assert item["detail"]["k"] == "v"
        # 关键：不落盘——当前工作目录及 tmp 下都不应有 logs.jsonl
        assert not (tmp / "logs.jsonl").exists()
    finally:
        logger.removeHandler(handler)
    print("  [ok] add 输出 stderr，不落盘")


def test_register_type_preserved(tmp: Path) -> None:
    """LOG_TYPE_REGISTER 常量与 add 入口保留（注册机依赖，CLAUDE.md 要求）。"""
    assert LOG_TYPE_REGISTER == "register"
    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    logger = logging.getLogger("chatgpt2api.log")
    logger.addHandler(handler)
    try:
        svc = LogService()
        svc.add(LOG_TYPE_REGISTER, "注册成功", {"email": "a@b.c"})
        item = json.loads(captured.getvalue().strip())
        assert item["type"] == "register"
        assert item["detail"]["email"] == "a@b.c"
    finally:
        logger.removeHandler(handler)
    print("  [ok] LOG_TYPE_REGISTER 入口保留")


def test_urls_truncation(tmp: Path) -> None:
    """单行 urls 截断：≤20 条 × ≤200 字符。"""
    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    logger = logging.getLogger("chatgpt2api.log")
    logger.addHandler(handler)
    try:
        svc = LogService()
        long_urls = ["x" * 500 for _ in range(50)]
        svc.add(LOG_TYPE_CALL, "big", {"urls": long_urls})
        item = json.loads(captured.getvalue().strip())
        urls = item["detail"]["urls"]
        assert len(urls) == LogService._MAX_URLS, f"应截断到 {LogService._MAX_URLS}，实为 {len(urls)}"
        assert all(len(u) <= LogService._MAX_URL_LEN for u in urls)
    finally:
        logger.removeHandler(handler)
    print(f"  [ok] 单行 urls 截断（{LogService._MAX_URLS} 条 × ≤{LogService._MAX_URL_LEN} 字符）")


def test_concurrent_add_threadsafe(tmp: Path) -> None:
    """多线程并发 add 不抛异常、不交错（每行是独立 JSON）。"""
    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    logger = logging.getLogger("chatgpt2api.log")
    logger.addHandler(handler)
    try:
        svc = LogService()

        def worker(idx: int) -> None:
            for i in range(50):
                svc.add(LOG_TYPE_CALL, f"t{idx}-{i}", {"idx": idx, "i": i})

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = [l for l in captured.getvalue().splitlines() if l.strip()]
        # 每行都应是合法 JSON（锁保证不交错）
        for line in lines:
            json.loads(line)
        assert len(lines) == 10 * 50, f"应 500 行，实为 {len(lines)}"
    finally:
        logger.removeHandler(handler)
    print(f"  [ok] 并发 add 线程安全（{10*50} 行全部合法 JSON）")


def main() -> None:
    failures = 0
    for fn in [
        test_add_outputs_stderr_no_file,
        test_register_type_preserved,
        test_urls_truncation,
        test_concurrent_add_threadsafe,
    ]:
        print(f"[RUN] {fn.__name__}")
        with tempfile.TemporaryDirectory() as d:
            # 切到 tmp 防 add 在 cwd 产生文件
            old = os.getcwd()
            os.chdir(d)
            try:
                fn(Path(d))
            except AssertionError as e:
                print(f"  [FAIL] {e}")
                failures += 1
            except Exception as e:  # noqa
                print(f"  [ERROR] {type(e).__name__}: {e}")
                failures += 1
            finally:
                os.chdir(old)
    print(f"\n{'='*40}")
    if failures:
        print(f"FAILED: {failures}")
        raise SystemExit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
