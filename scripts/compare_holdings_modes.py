"""振荡修复 backtest 对照：持仓重建三模式 vs 真实组合（翻转前置验证）

背景（docs 策略与风控 遗留 · 记忆 strategy-oscillation-fix-pending）：
实盘 `multi_factor._reconstruct_holdings` 把上一信号日 action ∈ {BUY, HOLD} 全视为持仓，
HOLD 把「未持有等回调」的股票误计为持仓 → mass-HOLD 日（08-19:719）与 mass-SELL 日
（08-20:704）隔日交替。修复方向 = 从真实持仓（position/trade 表）重建。

本脚本在回测引擎内对照三种 holdings_mode（replay.py 新增，同窗口同参数）：
  running      —— 回测现状（信号运行集，BUY 加/SELL 删，不忠实复现实盘振荡）
  reconstruct  —— 实盘现状语义（持仓 = 上一信号日 {BUY,HOLD}，复现振荡缺陷）
  portfolio    —— 修复方向（引擎同步真实组合持仓，成交失败不产生幽灵持仓）

输出：各模式 总收益/年化/回撤/夏普/turnover/成交笔数 + 每日信号计数统计
（mass-HOLD/mass-SELL 天数、相邻振荡次数、SELL 信号 vs SELL 成交 幻影量）。

用法（仓库根目录，quant-engine 环境 + DB 凭据）：
    set -a && source scripts/dev.env && set +a
    python3 scripts/compare_holdings_modes.py [--start 2024-01-01] [--end 2026-08-21]
        [--top-n 20] [--fill-mode t_close|t1_open|both] [--modes running,reconstruct,portfolio]
        [--detail]   # 逐日信号计数 CSV 输出
"""
import argparse
import json
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, "quant-engine")

MODES = ["running", "reconstruct", "portfolio"]


def build_strategy(db, top_n, holdings_mode, strategy_name="multi_factor"):
    from sqlalchemy import select

    from app.backtest.replay import ReplayStrategy
    from app.models.tables import FactorDefinition, Strategy as StrategyModel

    # 只选存在的列（dev DB 落后 2.3b，factor_definition 缺 version/status/params；
    # 全模型 select 会崩，列级 select 规避 schema 漂移）
    defs = db.execute(
        select(FactorDefinition.name, FactorDefinition.category,
               FactorDefinition.weight)
        .where(FactorDefinition.name.in_(
            ["ma_trend", "macd_signal", "pe_ratio", "pb_ratio",
             "roe_quality", "debt_risk"]))
    ).all()
    weights = {d[0]: float(d[2]) for d in defs}
    categories = {d[0]: d[1] for d in defs}
    params = {}
    row = db.execute(
        select(StrategyModel.name, StrategyModel.factor_weights,
               StrategyModel.params)
        .where(StrategyModel.name == strategy_name)).first()
    if row is not None:
        if row[2]:
            params = dict(row[2])
        if row[1]:
            weights.update(dict(row[1]))
    params["top_n"] = top_n
    params["holdings_mode"] = holdings_mode
    return ReplayStrategy(db, params, weights, categories)


def clone_strategy(base, holdings_mode):
    """浅拷贝预加载数据（series/grid 共享只读），holdings 状态独立"""
    from app.backtest.replay import ReplayStrategy
    s = ReplayStrategy.__new__(ReplayStrategy)
    for attr in ("db", "weights", "categories", "top_n", "buy_buffer",
                 "sell_buffer", "max_position_pct", "stop_loss_pct",
                 "drawdown_fuse_pct", "industry_limit_pct", "series", "grid",
                 "_date_pos", "pool", "industry"):
        setattr(s, attr, getattr(base, attr))
    s.holdings_mode = holdings_mode
    s.holdings = set()
    s._prev_signal_actions = {}
    return s


def instrument(strategy):
    """包装 generate_signal 记录每日信号计数（不侵入生产代码）"""
    orig = strategy.generate_signal
    counts = []

    def wrapped():
        sigs = orig()
        c = Counter(s.action for s in sigs)
        counts.append({"BUY": c["BUY"], "SELL": c["SELL"], "HOLD": c["HOLD"]})
        return sigs

    strategy.generate_signal = wrapped
    return counts


def run_mode(base, holdings_mode, start, end, fill_mode):
    from app.backtest.engine import BacktestEngine

    strat = clone_strategy(base, holdings_mode)
    counts = instrument(strat)
    engine = BacktestEngine(strat, start, end, db=base.db, fill_mode=fill_mode)
    report = engine.run()
    # 每日 SELL 成交笔数（幻影信号对照：SELL 信号 vs 实际 SELL 成交）
    sell_fills = Counter(t["date"] for t in engine.portfolio.trades
                         if t["action"] == "SELL")
    return {
        "mode": holdings_mode,
        "fill_mode": fill_mode,
        "report": report,
        "counts": counts,
        "dates": [r["date"] for r in engine.daily_returns],
        "sell_fills": sell_fills,
    }


def oscill_metrics(counts):
    """振荡统计：mass-HOLD/mass-SELL 天数、相邻振荡、SELL 信号总量"""
    bu = sum(1 for c in counts if c["BUY"] > 30)
    ho = sum(1 for c in counts if c["HOLD"] > 500)
    se = sum(1 for c in counts if c["SELL"] > 100)
    # 相邻振荡：mass-HOLD 日紧随 mass-SELL 日（或反之）的转换次数
    alt = 0
    for a, b in zip(counts, counts[1:]):
        a_hold = a["HOLD"] > 500
        b_sell = b["SELL"] > 100
        a_sell = a["SELL"] > 100
        b_hold = b["HOLD"] > 500
        if (a_hold and b_sell) or (a_sell and b_hold):
            alt += 1
    return {"mass_buy_days": bu, "mass_hold_days": ho, "mass_sell_days": se,
            "alternations": alt,
            "total_sell_signals": sum(c["SELL"] for c in counts)}


def fmt_pct(x):
    return f"{x * 100:+.2f}%" if x is not None else "N/A"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date(2026, 8, 21))
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--fill-mode", default="both",
                    choices=["t_close", "t1_open", "both"])
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--detail", action="store_true",
                    help="输出逐日信号计数 CSV")
    args = ap.parse_args()

    from app.db import get_session

    db = get_session()
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    fill_modes = ["t_close", "t1_open"] if args.fill_mode == "both" \
        else [args.fill_mode]

    start, end = args.start.isoformat(), args.end.isoformat()
    base = build_strategy(db, args.top_n, "running")
    print(f"预加载因子序列（{start} ~ {end}，top_n={args.top_n}）…", flush=True)
    base.preload(start, end)

    results = []
    for fill_mode in fill_modes:
        for mode in modes:
            print(f"  运行 {mode} × {fill_mode} …", flush=True)
            results.append(run_mode(base, mode, start, end, fill_mode))

    print("\n==================== 振荡修复 backtest 对照 ====================")
    print(f"区间 {start} ~ {end}（top_n={args.top_n}，{results[0]['report']['trading_days']} 个交易日）\n")
    for fm in fill_modes:
        print(f"--- fill_mode={fm} ---")
        hdr = (f"{'mode':<13}{'总收益':>9}{'年化':>9}{'回撤':>9}{'夏普':>7}"
               f"{'turnover':>9}{'成交':>6}{'末持仓':>7}{'massHOLD':>9}"
               f"{'massSELL':>9}{'振荡':>6}{'SELL信号':>9}")
        print(hdr)
        for r in results:
            if r["fill_mode"] != fm:
                continue
            p = r["report"]["portfolio"]
            om = oscill_metrics(r["counts"])
            print(
                f"{r['mode']:<13}"
                f"{fmt_pct(p['total_return']):>9}"
                f"{fmt_pct(p['annualized_return']):>9}"
                f"{fmt_pct(p['max_drawdown']):>9}"
                f"{(str(p['sharpe']) if p['sharpe'] is not None else 'N/A'):>7}"
                f"{str(r['report']['turnover']):>9}"
                f"{r['report']['trades']:>6}"
                f"{r['report']['positions']:>7}"
                f"{om['mass_hold_days']:>9}"
                f"{om['mass_sell_days']:>9}"
                f"{om['alternations']:>6}"
                f"{om['total_sell_signals']:>9}")
        print()

    # 幻影信号对照：reconstruct 的 SELL 信号 vs 实际 SELL 成交
    print("--- 幻影 SELL 对照（SELL 信号总量 vs 实际 SELL 成交笔数）---")
    for r in results:
        sells = sum(c["SELL"] for c in r["counts"])
        fills = len(r["sell_fills"])
        print(f"  {r['mode']:<13}×{r['fill_mode']:<8} SELL 信号 {sells:>6}  →  "
              f"SELL 成交 {fills:>4}  "
              f"（幻影 {sells - fills:>6}）")

    if args.detail:
        for r in results:
            fn = f"/tmp/holdings_{r['mode']}_{r['fill_mode']}.csv"
            with open(fn, "w") as f:
                f.write("date,buy,sell,hold,sell_fills\n")
                for c, d in zip(r["counts"], r["dates"]):
                    f.write(f"{d},{c['BUY']},{c['SELL']},{c['HOLD']},"
                            f"{r['sell_fills'].get(d, 0)}\n")
            print(f"\n逐日明细 → {fn}")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
