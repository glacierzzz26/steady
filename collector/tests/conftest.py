"""collector 测试全局守卫（Issue #14）。

请求层超时补丁**只能在进程入口安装**（tasks/cli/backfill 的 `__main__`），
import 期不装——测试会 `import app.tasks` / `app.collectors.daily`，若 import 期安装，
补丁会静默污染整个测试集（之后所有 `requests.get` 都拿到真实 socket 超时，
既有用例对异常的断言会奇怪地失效，且无任何提示）。

本 conftest 把这条不变式钉死：autouse fixture 在每个用例结束后断言补丁未被残留安装
（用例自己装的要自己卸）。`with_timeout` 的泄漏计数会跨用例累积，用
`reset_leak_counter` fixture 显式归零，避免污染后续对计数的断言。
"""
import pytest


@pytest.fixture(autouse=True)
def _no_patch_leak():
    """用例间不得残留请求层超时补丁（用 `bare_requests` fixture 显式安装 + 自动卸载）"""
    yield
    from app.sources.net import installed_timeouts

    assert installed_timeouts() is None, (
        "测试结束时请求层超时补丁仍处于安装态 —— 用例应使用 bare_requests fixture，"
        "或在 finally 里调用 uninstall_http_timeouts()")


@pytest.fixture
def bare_requests():
    """安装请求层超时补丁，用例结束后**必定**卸载。

    `bare` 取「裸 requests」义：打补丁后裸 `requests.get(url)` 才受超时保护。
    """
    from app.sources.net import install_http_timeouts, uninstall_http_timeouts

    installed = install_http_timeouts()
    try:
        yield installed
    finally:
        uninstall_http_timeouts()


@pytest.fixture
def reset_leak_counter():
    """把 with_timeout 的泄漏计数归零（用例结束后恢复为 0）。

    计数是模块级全局，会被任一强制超时的用例推高。断言计数的用例必须用本 fixture，
    否则执行顺序一变就飘。
    """
    from app.collectors import base

    with base._leak_lock:
        base._leaked_workers = 0
    yield
    with base._leak_lock:
        base._leaked_workers = 0
