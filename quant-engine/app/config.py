"""quant-engine 运行时配置（环境变量）。

**调用时读取**：全部以函数形式暴露（而非模块级常量），便于测试 monkeypatch，
并与既有先例 `data_quality._collect_scope()` 的「调用时读」风格一致。
"""
import os


def _str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _list(name: str, default: str) -> list[str]:
    """逗号列表 env → 去空字符串列表"""
    return [s.strip() for s in os.getenv(name, default).split(",") if s.strip()]


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "").strip().lower() in (
        "1", "true", "yes", "on")


# ---------- datahub 读闸门（Phase 3 · 灰度读切换）----------
# steady 按需调 datahub HTTP API 取原始数据，实现「单一真源」。
# 闸门 = base_url 非空 × token 非空 × dataset ∈ DATAHUB_READ_DATASETS；
# 默认全空 ⇒ 恒 False ⇒ 读本地库（**部署零行为变更**）。回退 = 清空白名单重启。
DATAHUB_DEFAULT_BASE_URL = "http://datahub:8100"


def datahub_base_url() -> str:
    return _str("DATAHUB_BASE_URL", DATAHUB_DEFAULT_BASE_URL).strip()


def datahub_token() -> str:
    return _str("DATAHUB_TOKEN", "").strip()


def datahub_read_datasets() -> list[str]:
    return _list("DATAHUB_READ_DATASETS", "")


def datahub_read_enabled(dataset: str) -> bool:
    """该数据集是否走 datahub 读路径（三条件同时满足；默认恒 False=读本地）。"""
    return (bool(datahub_base_url()) and bool(datahub_token())
            and dataset in datahub_read_datasets())


def datahub_connect_timeout() -> float:
    return _float("DATAHUB_HTTP_CONNECT_TIMEOUT", 5)


def datahub_read_timeout() -> float:
    return _float("DATAHUB_HTTP_READ_TIMEOUT", 10)


def datahub_retries() -> int:
    return _int("DATAHUB_RETRIES", 1)


def datahub_retry_delay() -> float:
    return _float("DATAHUB_RETRY_DELAY", 0.5)


def datahub_cache_ttl() -> float:
    return _float("DATAHUB_CACHE_TTL", 60)


def datahub_fallback_local() -> bool:
    """故障时是否回退本地读（默认 False=失败即抛，见阶段蓝图「决策」）。"""
    return _bool("DATAHUB_FALLBACK_LOCAL", False)
