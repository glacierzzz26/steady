"""一次性运维脚本：恢复生产指数成交额（amount）

背景：08-28 index 采集器切 AkShare 主源（新浪 stock_zh_index_daily 无成交额列），
build_rows 置 amount=None，save() upsert 全史带 amount 列把 BaoStock 回填的成交额
全清 NULL → G6 两市成交断供（market_hotspot.turnover 消失）。已修 index.py save()
（无 amount 时不更新该列）；本脚本用 BaoStock 全史回补 amount，恢复历史成交额。

用法（在 prod collector 容器内，凭据走容器 env）：
    docker cp scripts/restore_index_amount.py quant-collector:/tmp/
    docker exec quant-collector python3 /tmp/restore_index_amount.py

只 upsert amount 列（conflict_cols=code+trade_date），其余列不动。
"""
import sys
from datetime import date

sys.path.insert(0, "/app")

from app.db import get_session, upsert
from app.models.tables import DailyPrice
from app.sources import baostock

INDEXES = ["sh000001", "sh000300", "sh000905", "sz399106"]
START = date(1990, 1, 1)
END = date(2026, 8, 29)


def main():
    db = get_session()
    sess = baostock.get_session()
    if sess is None:
        print("❌ BaoStock 会话不可用，中止")
        return 1
    total = 0
    for code in INDEXES:
        rows = baostock.index_rows(sess, code, START, END)
        if not rows:
            print(f"⚠️  {code} BaoStock 未返回数据，跳过")
            continue
        # 只回补 amount；row 仅含 code/trade_date/amount → INSERT 只覆盖这三列
        upd = [{"code": r["code"], "trade_date": r["trade_date"],
                "amount": r["amount"]} for r in rows if r.get("amount") is not None]
        upsert(db, DailyPrice, upd,
               conflict_cols=["code", "trade_date"],
               update_cols=["amount"])
        n_amt = len(upd)
        total += n_amt
        latest = rows[-1]
        print(f"✅ {code} 回补 {n_amt} 行 amount"
              f"（{rows[0]['trade_date']} → {latest['trade_date']}，"
              f"最新 amount={latest['amount']}）")
    print(f"\n回补完成，共 {total} 行。")
    # 复检：各指数剩余 NULL amount 行数
    from sqlalchemy import text
    for code in INDEXES:
        n = db.execute(text(
            "SELECT count(*) FROM daily_price WHERE code = :c AND amount IS NULL"
        ).bindparams(c=code)).scalar()
        print(f"复检 {code}: amount NULL 剩余 {n} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
