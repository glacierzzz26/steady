"""数据库连接管理（SQLAlchemy，配置来自环境变量）"""
import os
from typing import Callable, Sequence

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def get_dsn() -> str:
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    user = os.getenv("DB_USER", "quant")
    password = os.getenv("DB_PASSWORD", "")
    name = os.getenv("DB_NAME", "quant_system")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


def create_db_engine() -> Engine:
    """创建数据库引擎（连接池：5 + 10）"""
    return create_engine(get_dsn(), pool_size=5, max_overflow=10, pool_pre_ping=True)


# 进程级单例引擎/会话工厂：连接池（5+10）整进程复用。
# 若每次 get_session 新建 engine，旧池 idle 连接依赖 GC 才关闭，
# 会缓慢堆积直到打满 Postgres max_connections（2026-08-30 生产事故）。
_engine: Engine | None = None
_session_factory = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session() -> Session:
    """获取一个复用进程级连接池的会话"""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine())
    return _session_factory()


def upsert(
    session: Session,
    model: type,
    rows: Sequence[dict],
    conflict_cols: Sequence[str],
    update_cols: Sequence[str],
    where: Callable[[object], object] | None = None,
) -> int:
    """批量 UPSERT 入库。

    :param conflict_cols: 冲突判定的列（对应 UNIQUE 索引）
    :param update_cols:   冲突时更新的列
    :param where:         可选回调，接收 excluded 对象返回 WHERE 条件
                         （如财务数据只覆盖 announce_date 更新的行）
    """
    if not rows:
        return 0
    stmt = pg_insert(model).values(list(rows))
    stmt = stmt.on_conflict_do_update(
        index_elements=list(conflict_cols),
        set_={col: stmt.excluded[col] for col in update_cols},
        where=where(stmt.excluded) if where else None,
    )
    session.execute(stmt)
    session.commit()
    return len(rows)
