"""入口点（`__main__` 块）冒烟：静态校验 + 真实启动（Issue #14）。

**为什么需要**：pytest 从不执行 `if __name__ == "__main__":` 块，入口里的错误在测试集里
**完全隐形**。Issue #14 就踩了这个：collector 的 `register_catchups()` 被调用在其定义
**之前** → `NameError` → 容器启动即崩、`restart: unless-stopped` 反复拉起（crash loop），
而全套单测全绿。本文件对 quant-engine 施加同样的两道防线。
"""
import ast
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
TASKS_PY = APP_DIR / "app" / "tasks.py"


def test_all_functions_called_in_main_are_defined_before_it():
    """静态守卫：`__main__` 里调用的模块级函数不得定义在其后（顺序执行的 NameError）"""
    tree = ast.parse(TASKS_PY.read_text(encoding="utf-8"))
    main = None
    for node in tree.body:
        if (isinstance(node, ast.If)
                and getattr(getattr(node.test, "left", None), "id", None) == "__name__"):
            main = node
            break
    assert main is not None, "未找到 __main__ 块"

    defs = {n.name: n.lineno for n in tree.body if isinstance(n, ast.FunctionDef)}
    called = {n.func.id for n in ast.walk(main)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    offenders = sorted(c for c in called if c in defs and defs[c] > main.lineno)
    assert not offenders, (
        f"{TASKS_PY.name} 的 __main__ 块调用了在其后才定义的函数 {offenders} —— "
        "模块体顺序执行，会 NameError 使进程启动即崩（Issue #14 真实事故）")


def test_entrypoint_starts_scheduler():
    """动态守卫：真起 `python -m app.tasks`，须走到 "Scheduler started"。

    `BlockingScheduler.start()` 永久阻塞（正常工作方式），故流式读日志、见目标行即终止。
    """
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 9201))
    except OSError:
        pytest.skip("9201 已被占用（本地已有 quant-engine 在跑），跳过入口冒烟")
    finally:
        probe.close()

    proc = subprocess.Popen(
        [sys.executable, "-m", "app.tasks"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(APP_DIR), bufsize=1)

    lines: list[str] = []
    ok = False
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break  # 进程已退出（崩溃）
            lines.append(line)
            if "Scheduler started" in line:
                ok = True
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)

    tail = "".join(lines[-40:])
    assert ok, f"入口未能启动调度器：\n{tail}"
    assert "NameError" not in tail and "Traceback" not in tail, \
        f"入口启动过程抛异常：\n{tail}"
