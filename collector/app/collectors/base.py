"""采集器基类：统一异常处理与重试逻辑"""
import concurrent.futures
import logging
import time
from abc import ABC, abstractmethod
from datetime import date, datetime

from app.config import REQUEST_TIMEOUT


def to_ak_date(value) -> str:
    """把 date / datetime / ISO 字符串统一成 AkShare 需要的 YYYYMMDD"""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "").replace("/", "")


def with_timeout(fn, *args, timeout=None, **kwargs):
    """在线程内执行 AkShare 请求并施加超时；超时抛 TimeoutError。

    AkShare 底层 requests 未设置 timeout，对端半开连接时会永久挂起
    （曾因此卡死整个同步）。此包装器兜底：超时抛 TimeoutError，
    由调用方按"降级/重试"处理，而不是无限等待。
    """
    if timeout is None:
        timeout = REQUEST_TIMEOUT
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        try:
            return ex.submit(fn, *args, **kwargs).result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"请求超时（>{timeout}s）")


class BaseCollector(ABC):
    """所有采集器的基类。

    子类需实现 fetch()（拉取数据）与 save()（入库），
    run() 提供统一的重试与日志框架。
    """

    max_retries = 3
    retry_delay = 5  # seconds

    def __init__(self, db_session):
        self.db = db_session
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def fetch(self, *args, **kwargs):
        """从数据源拉取数据，返回记录列表"""
        raise NotImplementedError

    @abstractmethod
    def save(self, data):
        """将数据保存到数据库"""
        raise NotImplementedError

    def run(self, *args, **kwargs):
        """执行采集（带重试）"""
        for attempt in range(1, self.max_retries + 1):
            try:
                data = self.fetch(*args, **kwargs)
                self.save(data)
                self.logger.info("采集成功: %s 条", len(data))
                return True
            except Exception as e:
                self.logger.warning("第 %s 次重试: %s", attempt, e)
                # 回滚失败事务，否则后续重试报 current transaction is aborted
                try:
                    self.db.rollback()
                except Exception:
                    pass
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
        self.logger.error("采集失败，已达最大重试次数")
        return False
