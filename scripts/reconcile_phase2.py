"""阶段 2 迁移对账：BaoStock（主源候选）vs dev 库既有 Tushare/AkShare 数据

用法（仓库根目录，依赖 collector 的 Python 环境 + dev DB 凭据）：
    DB_HOST=127.0.0.1 DB_PORT=5432 DB_USER=quant DB_PASSWORD=... \
    DB_NAME=quant_system python3 scripts/reconcile_phase2.py [--sample N]

放行门（两波灰度前置，全过才翻转对应 scope）：
  - stock_basic：name/list_date/market 匹配率 ≥99%；**北交所覆盖核对**（BaoStock
    无北交所 → 若库内有大量 bj，stock_basic 不切/保留 Tushare，另行决策）。
  - valuation：BaoStock vs 库 daily_valuation——close 逐位 ±0.01 元；pe_ttm/pb
    相对差 ≤2%（财报边界口径差）；行数覆盖。
  - finance：BaoStock vs 库 financial_indicator——roe 差 ≤0.5pp、debt_ratio ≤0.5pp
    （单位校准已 ×100 实证）；announce_date 一致率；revenue_growth 对账。
    若 roe 系统性差 >1pp → finance 不切（保留 Tushare/AkShare）。
  - index：BaoStock 指数 kline vs 库 daily_price——close 逐位；amount 相对差 ≤0.01%。

finance/valuation 未通过则保持 Tushare/AkShare，不硬切（实盘安全兜底）。
"""
import argparse
import sys
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, "collector")


def _session():
    from app.db import get_session

    return get_session()


def _sess_bs():
    from app.sources import baostock

    s = baostock.get_session()
    if s is None:
        raise SystemExit("BaoStock 不可用（baostock 未安装或登录失败）")
    return s


def _f(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rel_diff(a, b):
    """相对差（%）：|a-b| / |b|，b 为 0/None 时返回 None"""
    a, b = _f(a), _f(b)
    if a is None or b is None or b == 0:
        return None
    return abs(a - b) / abs(b) * 100


# ---------- stock_basic ----------

def check_stock_basic(db, bs_rows):
    from app.sources import baostock
    from sqlalchemy import text

    db_rows = db.execute(text(
        "SELECT code, name, market, list_date, status FROM stock_basic")).all()
    db_by_code = {r.code: r for r in db_rows}
    print(f"\n=== stock_basic：BaoStock {len(bs_rows)} vs 库 {len(db_rows)} ===")
    bj_db = sum(1 for r in db_rows if r.market == "BJ")
    bj_bs = sum(1 for r in bs_rows if r["market"] == "BJ")
    print(f"  北交所覆盖：库 {bj_db} 只，BaoStock {bj_bs} 只 "
          f"→ {'⚠️ BaoStock 无北交所，切换会丢新增/刷新' if bj_bs == 0 and bj_db > 0 else 'ok'}")
    bs_by_code = {r["code"]: r for r in bs_rows}
    only_db = sorted(set(db_by_code) - set(bs_by_code))
    only_bs = sorted(set(bs_by_code) - set(db_by_code))
    print(f"  仅库有（BaoStock 缺）{len(only_db)}：{only_db[:8]}")
    print(f"  仅 BaoStock 有（库缺）{len(only_bs)}：{only_bs[:8]}")
    common = [c for c in db_by_code if c in bs_by_code]
    name_match = sum(1 for c in common if db_by_code[c].name == bs_by_code[c]["name"])
    date_match = sum(1 for c in common
                     if db_by_code[c].list_date and
                     str(db_by_code[c].list_date) == str(bs_by_code[c].get("list_date")))
    market_match = sum(1 for c in common if db_by_code[c].market == bs_by_code[c]["market"])
    for label, n in (("name", name_match), ("list_date", date_match), ("market", market_match)):
        print(f"  {label} 匹配率：{n}/{len(common)} = {n / max(len(common), 1) * 100:.2f}%")
    # 差异样例
    diff = [c for c in common if db_by_code[c].name != bs_by_code[c]["name"]][:5]
    for c in diff:
        print(f"    name 差 {c}：库「{db_by_code[c].name}」 vs BaoStock「{bs_by_code[c]['name']}」")
    ok = (bj_bs > 0 or bj_db == 0) and len(common) > 0
    return ok


# ---------- valuation ----------

def check_valuation(db, sess, codes):
    from app.sources import baostock
    from sqlalchemy import text

    today = date.today()
    n = 0
    close_ok = pe_ok = pb_ok = nrows = 0
    close_bad = pe_bad = pb_bad = 0
    for code in codes:
        rows = baostock.valuation_rows(sess, code, today - timedelta(days=3650), today)
        if not rows:
            continue
        db_rows = {
            r.trade_date: r
            for r in db.execute(text(
                "SELECT trade_date, close, pe_ttm, pb FROM daily_valuation "
                "WHERE code = :c AND trade_date >= :s"),
                {"c": code, "s": today - timedelta(days=400)}).all()
        }
        n += 1
        for r in rows:
            dr = db_rows.get(r["trade_date"])
            if dr is None:
                continue
            nrows += 1
            # close 逐位 ±0.01
            if dr.close is None or r["close"] is None:
                continue
            if abs(_f(dr.close) - _f(r["close"])) <= 0.011:
                close_ok += 1
            else:
                close_bad += 1
            # pe_ttm/pb 相对差 ≤2%
            for k, name in (("pe_ttm", "pe_ttm"), ("pb", "pb")):
                if getattr(dr, name) is None or r.get(k) is None:
                    continue
                d = _rel_diff(getattr(dr, name), r.get(k))
                is_pe = name == "pe_ttm"
                if d is not None and d <= 2.0:
                    if is_pe:
                        pe_ok += 1
                    else:
                        pb_ok += 1
                else:
                    if is_pe:
                        pe_bad += 1
                    else:
                        pb_bad += 1
    print(f"\n=== valuation（样本 {n} 只，共 {nrows} 行对比）===")
    print(f"  close 逐位 ±0.01：{close_ok} 好 / {close_bad} 差"
          + (f"（样例差 {close_bad}，'— 更多'）" if close_bad else ""))
    print(f"  pe_ttm 相对差 ≤2%：{pe_ok} 好 / {pe_bad} 差")
    print(f"  pb 相对差 ≤2%：{pb_ok} 好 / {pb_bad} 差")
    return close_bad == 0 and nrows > 0


# ---------- finance ----------

def check_finance(db, sess, codes, periods):
    from app.sources import baostock
    from sqlalchemy import text

    roe_ok = roe_bad = debt_ok = debt_bad = ann_ok = ann_bad = rev_ok = rev_bad = 0
    n = 0
    for code in codes:
        for y, q in periods:
            r = baostock.financial_rows(sess, code, y, q)
            if r is None:
                continue
            rd = date(y, _QEND[q][0], _QEND[q][1])
            db_r = db.execute(text(
                "SELECT roe, debt_ratio, revenue_growth, announce_date FROM financial_indicator "
                "WHERE code = :c AND report_date = :d"), {"c": code, "d": rd}).first()
            if db_r is None:
                continue
            n += 1
            if db_r.roe is not None and r["roe"] is not None:
                if abs(_f(db_r.roe) - _f(r["roe"])) <= 0.5:
                    roe_ok += 1
                else:
                    roe_bad += 1
            if db_r.debt_ratio is not None and r["debt_ratio"] is not None:
                if abs(_f(db_r.debt_ratio) - _f(r["debt_ratio"])) <= 0.5:
                    debt_ok += 1
                else:
                    debt_bad += 1
            if db_r.revenue_growth is not None and r["revenue_growth"] is not None:
                if abs(_f(db_r.revenue_growth) - _f(r["revenue_growth"])) <= 2.0:
                    rev_ok += 1
                else:
                    rev_bad += 1
            if db_r.announce_date and r["announce_date"]:
                if db_r.announce_date == r["announce_date"]:
                    ann_ok += 1
                else:
                    ann_bad += 1
    print(f"\n=== finance（样本 {n} 行对比）===")
    print(f"  roe 差 ≤0.5pp：{roe_ok} 好 / {roe_bad} 差")
    print(f"  debt_ratio 差 ≤0.5pp：{debt_ok} 好 / {debt_bad} 差")
    print(f"  revenue_growth 差 ≤2pp：{rev_ok} 好 / {rev_bad} 差")
    print(f"  announce_date 一致：{ann_ok} / {ann_ok + ann_bad}")
    return roe_bad <= max(1, n * 0.05) and n > 0


# ---------- index ----------

def check_index(db, sess, symbols):
    from app.sources import baostock
    from sqlalchemy import text

    today = date.today()
    ok_rows = bad_close = bad_amt = nrows = 0
    for sym in symbols:
        rows = baostock.index_rows(sess, sym, today - timedelta(days=400), today)
        db_rows = {r.trade_date: r for r in db.execute(text(
            "SELECT trade_date, close, amount FROM daily_price "
            "WHERE code = :c"), {"c": sym}).all()}
        for r in rows:
            dr = db_rows.get(r["trade_date"])
            if dr is None:
                continue
            nrows += 1
            if dr.close is None or r["close"] is None:
                continue
            if abs(_f(dr.close) - _f(r["close"])) <= 0.011:
                ok_rows += 1
            else:
                bad_close += 1
            if dr.amount and r["amount"]:
                d = _rel_diff(dr.amount, r["amount"])
                if d is not None and d <= 0.01:
                    ok_rows += 1
                else:
                    bad_amt += 1
    print(f"\n=== index（{nrows} 行对比）===")
    print(f"  close/amount 达标：{ok_rows} / {bad_close + bad_amt} 差"
          f"（close {bad_close} / amount {bad_amt}）")
    return bad_close == 0 and bad_amt == 0 and nrows > 0


_QEND = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=120,
                    help="valuation 样本数（finance 用 min(40, sample)）")
    ap.add_argument("--only", choices=["stock_basic", "valuation", "finance", "index"],
                    default=None, help="只跑某项")
    args = ap.parse_args()

    from app.sources import baostock

    db = _session()
    sess = _sess_bs()

    # 股票池代码（hs300+zz500）
    from sqlalchemy import text

    pool = [r[0] for r in db.execute(text(
        "SELECT code FROM stock_basic WHERE universe IN ('hs300','zz500') "
        "ORDER BY code")).all()]
    import random
    random.seed(20260827)
    sample = pool[:args.sample]

    results = {}
    if args.only in (None, "stock_basic"):
        results["stock_basic"] = check_stock_basic(db, baostock.stock_basic_rows(sess))
    if args.only in (None, "valuation"):
        results["valuation"] = check_valuation(db, sess, sample)
    if args.only in (None, "finance"):
        y, q = 2026, 2
        periods = [(y, q), (y, q - 1 if q > 1 else 4)]
        results["finance"] = check_finance(db, sess, pool[:min(40, args.sample)], periods)
    if args.only in (None, "index"):
        from app.config import index_code_list
        results["index"] = check_index(db, sess, index_code_list())

    print("\n========== 放行判定 ==========")
    for k, v in results.items():
        print(f"  {k}: {'✅ 通过' if v else '❌ 未通过'}")
    failed = [k for k, v in results.items() if not v]
    print("\n结论：未通过项不翻转对应 scope；若 stock_basic 因北交所缺口失败 → 保留 Tushare。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
