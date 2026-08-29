"""阶段 3/4：adj_factor 全史重写为 AkShare 派生因子（东财→新浪主源，BaoStock 兜底；
真异常股保留 DB 既有值）

用法（仓库根目录，依赖 collector 的 Python 环境 + DB 凭据）：
    DB_HOST=127.0.0.1 DB_PORT=5432 DB_USER=quant DB_PASSWORD=... \
    DB_NAME=quant_system python3 scripts/rewrite_adj_factor.py [--start 2016-01-01] [--dry-run] [--codes ...]

作用域：--start 默认 2016-01-01——dev/prod 的 DB 复权因子覆盖均自 2016-08-01 起，
2016 年前无既有因子（NULL），拉 1990+ 全史只引入远古异常拒收噪声（000001 1991、
000002 1992、000009 1996 等，对 2016+ 重写无操作意义）且耗时翻倍。需要回填远古
因子时显式传 --start 1990-01-01。

对股票池（universe in hs300/zz500，默认全池）逐股：
  1. AkShare fetch_pair（东财→新浪主源，失败 BaoStock 兜底）（start→end）
     → build_rows → guard_factor（区间内判定）；
  2. 比值对账（DB 既有因子 / 源派生因子，应恒为常数，锚点抵消）+ DB 因子自洽校验。
     **DB 侧默认取全部既有行；迁移期须传 --db-asof 截止到独立（Tushare）记录末日**
     （prod=2026-08-27），否则采集器自写的 AkShare 派生尾巴混入对账，虚报漂移：
     - guard 通过 + 比值 ≤0.5% 或 无 DB 因子可比对 → 接受 → upsert 覆盖（只动因子列）；
     - guard 通过 + 比值漂移 >0.5% + DB 因子自洽（阶跃对应价格跳空）→ 跨源口径分歧
       （如 000002 万科 1.96%）→ 保留 DB 既有值；
     - guard 通过 + 比值漂移 >0.5% + DB 因子不自洽 → **DB 侧虚假调整**
       （如 601699 2026-08-26 单日因子骤降 17% 价格反涨，Tushare glitch）→ 判定改写源值；
     - guard 拒收 + 比值一致 → 守卫误伤（纯大跌日无除权，step≈gap 被 7% 下限误伤，
       如 000009 2021-08-26）→ 判定可重写（两源一致，写源值等价安全）；
     - guard 拒收 + 比值漂移 → 真异常（平安 2020-12-31 / 天齐 2020-01-02 虚假/滞后
       调整）→ 保留 DB 既有正确值。
  601155 型（东财假缩股，guard 大阶跃容差放行但 DB↔源比值断裂）→ 比值漂移 + DB 自洽
  → 保留 DB（08-28 手工校准值），不落东财假因子。

原则：接受集 per-stock 单源一致（比值口径锚点抵消，历史跨切换日无假跳变）；
真异常/漂移股保留既有值——两集各自内部一致，无跨源混用（设计文档 §2.2 连续性要求）。

--dry-run 只计算接受/拒收统计不写库（供 dev 对账调优容差）。
--reconcile 对账模式（隐含不写库）：逐股比对 + 分类输出，作为翻转前置门（Step 7）。
"""
import argparse
import sys
import time
from datetime import date

sys.path.insert(0, "collector")


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def classify(ok: bool, db_ok: bool, drift: bool, ratios: bool) -> str:
    """对账分类（纯函数，便于测试；test_rewrite_adj_factor.py 单测）。

    ok:    源派生因子（AkShare/BaoStock）guard 是否通过（判据 = 因子阶跃须对应价格跳空）；
    db_ok: DB 既有因子对 DB 既有价格的自洽校验结果（同 guard，独立跑在 DB 行上）；
    drift: DB↔源比值漂移 >0.5%（drift=True 隐含 ratios 非空）；
    ratios: 是否有可比对的 DB/源重叠因子行。

    返回分类键：
      accepted    → 可重写（guard 通过 + 比值一致，或填补 NULL）；
      db_anomaly  → 可重写（比值漂移但 DB 因子不自洽 = DB 侧虚假调整，如 601699）；
      false_pos   → 可重写（guard 拒收但比值一致 = 大跌日误伤，两源等价）；
      drifted     → 保留 DB（两源自洽但跨源口径分歧，如 000002）；
      rejected    → 保留 DB（guard 拒收且比值漂移/不可比 = 真异常，如 000001）。

    判定顺序（2026-08-29 修正）：`db_anomaly` 优先于源守卫误伤短路。2026-08-29 生产
    dry-run 实证：601699/601567/600460 类 DB 侧 glitch 股，源（新浪）因子在熔断/暴跌日
    （2016-01-06 等）被守卫误伤 → 原顺序 `not ok` 先返回 rejected，掩蔽了「DB 因子不自洽
    应改写」的判定，glitch 滞留 DB。重排后：DB 因子不自洽（db_ok=False）+ 比值漂移
    → 判定改写源值（源在 DB glitch 处与 DB 分歧，源可信）。平安/天齐型真异常不受影响：
    DB/Tushare 自洽（db_ok=True）→ 不走此分支，仍按 `not ok` → rejected 保留。
    """
    if drift and not db_ok:
        return "db_anomaly"
    if not ok:
        return "rejected" if (drift or not ratios) else "false_pos"
    if drift:
        return "drifted"
    return "accepted"


def main():
    ap = argparse.ArgumentParser()
    # DB 复权因子覆盖自 2016-08-01（dev/prod 一致）；默认 2016 起避开远古噪声
    ap.add_argument("--start", type=_parse_date, default=date(2016, 1, 1))
    ap.add_argument("--end", type=_parse_date, default=date.today())
    ap.add_argument("--db-asof", type=_parse_date, default=None,
                    help="DB 侧独立记录截止日：仅 trade_date <= db-asof 的 DB 行参与比值对账"
                         "与自洽校验。迁移期 DB 尾巴（2026-08-28 起 prod 日常采集切 AkShare/"
                         "新浪派生因子写库）已非 Tushare 独立记录，混入比值会对账虚报漂移"
                         "（2026-08-29 dry-run 实证 114/125 拒收为 08-28 尾巴伪象）；"
                         "传最后 Tushare 日（prod=2026-08-27）恢复对账语义。")
    ap.add_argument("--codes", help="逗号分隔股票池子集（默认 universe in hs300/zz500 全池）")
    ap.add_argument("--dry-run", action="store_true", help="只计算不写库")
    ap.add_argument("--reconcile", action="store_true",
                    help="对账：比值偏差 ≤0.5%% 分类输出（隐含不写库）")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 只（试跑）")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="每只间 sleep 秒数（限频；免费源并发/突发过载会触发"
                         "黑名单 10001011，全量重跑建议 0.2-0.5，配合低并发分片）")
    args = ap.parse_args()

    from app.db import get_session, upsert
    from app.models.tables import DailyPrice
    from app.sources import baostock
    from app.collectors.base import to_ak_date
    from app.collectors.daily import build_rows, fetch_pair
    from app.cleaners.factor_guard import guard_factor
    from sqlalchemy import text

    db = get_session()

    def fetch_source_rows(code):
        """AkShare 主源（东财→新浪）→ BaoStock 兜底 → build_rows 行列表。
        双源均失败返回 None；源返回空返回 []。"""
        try:
            raw, hfq = fetch_pair(code, to_ak_date(args.start), to_ak_date(args.end))
            if not raw.empty:
                return build_rows(code, raw, hfq)
        except Exception as e:
            print(f"  {code} AkShare 拉取异常({e})，尝试 BaoStock 兜底")
        try:
            sess = baostock.get_session()
            if sess is None:
                return None
            raw, hfq = baostock.daily_pairs(sess, code, args.start, args.end)
        except Exception as e:
            print(f"  {code} BaoStock 兜底也失败({e})")
            return None
        if raw.empty:
            return []
        return build_rows(code, raw, hfq)

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = [r[0] for r in db.execute(text(
            "SELECT code FROM stock_basic WHERE universe IN ('hs300','zz500') "
            "ORDER BY code")).all()]
    if args.limit:
        codes = codes[:args.limit]

    accepted, rejected, drifted, false_pos, db_anomaly = [], [], [], [], []
    skipped, failed = 0, []
    drift_detail = {}
    consec_fail = 0
    for i, code in enumerate(codes, 1):
        if args.sleep and i > 1:
            time.sleep(args.sleep)  # 逐股限频：免费源并发/突发过载会封禁
        # 单股拉取容错：双源（AkShare/BaoStock）偶发网络抖动/连接级失败 → 跳过该股
        # 不整片崩溃；连续 3 只失败判定连接故障，中止本片（避免逐股空转）
        rows = fetch_source_rows(code)
        if rows is None:
            consec_fail += 1
            failed.append(code)
            print(f"[{i}/{len(codes)}] {code} ⚠️ 双源拉取异常，跳过待重试")
            if consec_fail >= 3:
                print(f"连续 {consec_fail} 只拉取异常，判定连接故障，中止本片（余下待重跑）")
                break
            continue
        consec_fail = 0
        if not rows:
            skipped += 1
            print(f"[{i}/{len(codes)}] {code} 无数据，跳过")
            continue
        _, ok = guard_factor(rows)
        # 比值对账（对账/重写共用）：DB 既有因子 / 源派生因子应全史恒定
        # （锚点抵消；两源因子相对各自基期，同一除权事件下比值恒为一常数）。
        # 另取 DB close 跑 DB 侧自洽校验：DB 因子阶跃须对应 DB 价格跳空——比值漂移时
        # 据此区分「跨源口径分歧」（DB 自洽 → 保留，如 000002）vs「DB 侧虚假调整」
        # （DB 不自洽，如 601699 2026-08-26 Tushare 单日 glitch → 改写源值）。
        if args.db_asof:
            db_rows_raw = db.execute(text(
                "SELECT trade_date, adj_factor, close FROM daily_price "
                "WHERE code = :c AND adj_factor IS NOT NULL AND trade_date <= :asof"
            ).bindparams(c=code, asof=args.db_asof)).all()
        else:
            db_rows_raw = db.execute(text(
                "SELECT trade_date, adj_factor, close FROM daily_price "
                "WHERE code = :c AND adj_factor IS NOT NULL").bindparams(c=code)).all()
        db_map = {d: float(f) for d, f, _ in db_rows_raw if f is not None}
        db_rows = [{"trade_date": d, "adj_factor": float(f), "close": float(c)}
                   for d, f, c in db_rows_raw
                   if f is not None and c is not None]
        _, db_ok = guard_factor(db_rows)
        ratios = [db_map[r["trade_date"]] / r["adj_factor"]
                  for r in rows
                  if r.get("adj_factor") is not None
                  and r["adj_factor"] > 0
                  and r["trade_date"] in db_map
                  and db_map[r["trade_date"]] > 0]
        if ratios:
            med = sorted(ratios)[len(ratios) // 2]
            max_dev = max(abs(x / med - 1) for x in ratios)
            drift = max_dev > 0.005
        else:
            max_dev, drift = 0.0, False
        drift_detail[code] = (len(ratios), max_dev)
        bucket = classify(ok, db_ok, drift, bool(ratios))
        if args.reconcile:
            if bucket == "rejected":
                rejected.append(code)
                print(f"[{i}/{len(codes)}] {code} ❌ 守卫拒收"
                      f"（比值{'漂移 ' + f'{max_dev:.3%}' if drift else '不可比'}）→ 保留 DB 既有因子")
            elif bucket == "false_pos":
                false_pos.append(code)
                print(f"[{i}/{len(codes)}] {code} ⚠️ 守卫拒收但比值一致"
                      f" {max_dev:.3%}（大跌日误伤）→ 判定可重写")
            elif bucket == "db_anomaly":
                db_anomaly.append(code)
                print(f"[{i}/{len(codes)}] {code} ⚠️ 比值漂移 {max_dev:.3%} 且 DB 因子自洽校验失败"
                      f"（{len(ratios)} 行可比，DB 侧虚假调整）→ 判定改写源值")
            elif bucket == "drifted":
                drifted.append(code)
                print(f"[{i}/{len(codes)}] {code} ⚠️ 比值漂移 {max_dev:.3%}"
                      f"（{len(ratios)} 行可比，DB 自洽 → 保留 DB 既有值）")
            elif not ratios:
                accepted.append(code)
                print(f"[{i}/{len(codes)}] {code} ⚠️ 无重叠 DB 因子可比对 → 视为可重写（填补 NULL）")
            else:
                accepted.append(code)
                print(f"[{i}/{len(codes)}] {code} ✅ 接受"
                      f"（{len(ratios)} 行因子，比值偏差 ≤{max_dev:.3%}）")
            continue
        # 重写模式：真异常（guard 拒收）/ 跨源口径分歧（drift + DB 自洽）→ 跳过保留 DB；
        # 其余（接受 / 守卫误伤 / DB 侧异常）→ 写源派生因子
        if bucket in ("rejected", "drifted"):
            bucket_list = rejected if bucket == "rejected" else drifted
            bucket_list.append(code)
            label = "❌ 守卫拒收" if bucket == "rejected" else "⚠️ 比值漂移"
            print(f"[{i}/{len(codes)}] {code} {label} {max_dev:.3%}"
                  f"（{len(ratios)} 行可比，保留 DB 既有因子）")
            continue
        if bucket == "db_anomaly":
            db_anomaly.append(code)
        elif bucket == "false_pos":
            false_pos.append(code)
        else:
            accepted.append(code)
        # 只重写 adj_factor，其余列不动
        upd = [{"code": r["code"], "trade_date": r["trade_date"],
                "adj_factor": r["adj_factor"]}
               for r in rows if r.get("adj_factor") is not None]
        if not args.dry_run and upd:
            upsert(db, DailyPrice, upd,
                   conflict_cols=["code", "trade_date"],
                   update_cols=["adj_factor"])
        print(f"[{i}/{len(codes)}] {code} ✅ 改写（{bucket}，{len(upd)} 行因子）")

    if args.reconcile:
        print("\n========== 对账结果（翻转前置门）==========")
        print(f"接受 {len(accepted)} 只（比值 ≤0.5% 或填补 NULL）")
        print(f"守卫误伤 {len(false_pos)} 只（拒收但比值一致，判定可重写）：{false_pos}")
        print(f"DB 侧异常 {len(db_anomaly)} 只（比值漂移 + DB 因子不自洽，判定改写）：{db_anomaly}")
        print(f"守卫拒收 {len(rejected)} 只 → 保留 DB 既有值：{rejected}")
        for c in rejected:
            n, dev = drift_detail[c]
            print(f"   {c}: 可比 {n} 行，最大偏差 {dev:.3%}")
        print(f"比值漂移 {len(drifted)} 只 → 保留 DB 既有值：{drifted}")
        for c in drifted:
            n, dev = drift_detail[c]
            print(f"   {c}: 可比 {n} 行，最大偏差 {dev:.3%}")
        print(f"跳过 {skipped} 只（无数据）")
        print(f"拉取失败 {len(failed)} 只（需重跑）：{failed}")
    else:
        print("\n========== 重写结果 ==========")
        print(f"改写 {len(accepted) + len(false_pos) + len(db_anomaly)} 只"
              f"（接受 {len(accepted)} + 误伤 {len(false_pos)} + DB 异常 {len(db_anomaly)}）"
              f"→ adj_factor 切换为 AkShare 派生")
        print(f"拒收 {len(rejected)} 只 → 保留 DB 既有 Tushare 值：{rejected}")
        print(f"比值漂移 {len(drifted)} 只 → 保留 DB 既有值：{drifted}")
        print(f"跳过 {skipped} 只（无数据）")
        print(f"拉取失败 {len(failed)} 只（需重跑）：{failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
