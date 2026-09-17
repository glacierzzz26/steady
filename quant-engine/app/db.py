"""数据库连接管理（与 collector 共用同一套环境变量约定）"""
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session


def get_dsn() -> str:
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    user = os.getenv("DB_USER", "quant")
    password = os.getenv("DB_PASSWORD", "")
    name = os.getenv("DB_NAME", "quant_system")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


def create_db_engine() -> Engine:
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
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine())
    return _session_factory()


def upsert(session: Session, model, rows: list[dict],
           conflict_cols: list[str], update_cols: list[str]) -> int:
    """Postgres INSERT ... ON CONFLICT DO UPDATE（与 collector/app/db.py 同模式）"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not rows:
        return 0
    stmt = pg_insert(model).values(list(rows))
    stmt = stmt.on_conflict_do_update(
        index_elements=list(conflict_cols),
        set_={col: stmt.excluded[col] for col in update_cols},
    )
    session.execute(stmt)
    session.commit()
    return len(rows)
