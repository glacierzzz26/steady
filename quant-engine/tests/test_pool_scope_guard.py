"""策略选股域闸门（Issue #13 R7）：pool_codes() 必须只认 universe。

**这是扩池不改变策略口径的唯一硬证据。** 若后人把 `pool_codes()` 改成读
`data_scope`（或 IN 全量），factor_value 域会从 800 变 5212，选股结果与历史
`strategy_perf` 序列**质变**然而是静默的 —— 全量 pytest 仍会绿。故此处用
「data_scope='a_share' 但 universe=NULL 的股票不得进 pool」把这条钉死。
"""
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.factor_service import pool_codes
from app.models.tables import Base, StockBasic


def make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    # 池内（universe 有值 + data_scope 有值）
    db.add(StockBasic(code="600519", name="贵州茅台", market="SH",
                      universe="hs300", data_scope="a_share"))
    db.add(StockBasic(code="000001", name="平安银行", market="SZ",
                      universe="zz500", data_scope="a_share"))
    # 扩采集范围但**不在策略池**（universe=NULL，data_scope='a_share'）
    # —— 这是扩池的核心：采了但不选。
    db.add(StockBasic(code="002415", name="海康威视", market="SZ",
                      data_scope="a_share"))
    db.add(StockBasic(code="688111", name="金山办公", market="SH",
                      data_scope="a_share"))
    db.commit()


def test_pool_codes_only_reads_universe():
    """pool_codes() = universe 池；data_scope='a_share' 的非池股**绝不**入选"""
    db = make_db()
    _seed(db)
    assert pool_codes(db) == ["000001", "600519"]


def test_pool_codes_ignores_data_scope_entirely():
    """把非池股的 data_scope 清空/改值，pool_codes() 结果不变（与 data_scope 解耦）"""
    db = make_db()
    _seed(db)
    before = pool_codes(db)
    db.execute(select(StockBasic))  # noop
    for r in db.query(StockBasic).all():
        r.data_scope = None
    db.commit()
    assert pool_codes(db) == before
