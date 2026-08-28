# 阶段 3：去 Tushare 依赖（adj_factor 全史切 BaoStock 派生 + 因子守卫 + 配置面清除）

> 归档模板：每个阶段一页，按五节填写。设计定稿见 [`../design/数据源评估-BaoStock.md`](../design/数据源评估-BaoStock.md)（§5 阶段 3 路径）。
> 关联：进度总表 `../../进度总表.md`（待办 · 数据源稳定性 阶段 3）· 阶段 2 归档 [`BaoStock迁移-阶段2.md`](BaoStock迁移-阶段2.md)。
> 前置：阶段 1/2 已切 daily/calendar/index/valuation/finance 到 BaoStock 主源（AkShare 兜底），本阶段移除 Tushare 最后依赖。

## 目标

延续「数据源稳定性」，**移除全部 Tushare 依赖**：
1. **采集器零 Tushare 代码**：daily/valuation/index/calendar/finance/stock 全删 Tushare 分支，兜底链统一 BaoStock → AkShare；`sources/tushare.py` 删除（git 历史可恢复）。
2. **adj_factor 全史切 BaoStock 派生**：`build_rows` 由 hfq/raw 派生因子，经**除权一致性守卫**（`factor_guard`）校验「因子阶跃必须对应价格跳空」后才入库；真异常股（平安 2020-12-31 虚假调整 16.8%、天齐 2020-01-02 滞后调整 18.9%）保留 DB 既有 Tushare 正确值。
3. **backend/frontend 无 Tushare 配置面**：删 `TushareConfigService`、`/config/tushare` 路由与 handler、前端 Settings Tushare 区块；`migrate.sh` 幂等删 `app_config.tushare.token`（`004_delete_tushare_token.sql`）。

**用户约束（2026-08-28）**：生产升级（Step 8）不得在交易时间执行，须收盘后。

## 时间

| 起 | 止 | 说明 |
|---|---|---|
| 2026-08-28 | 2026-08-28 | 采集器去 Tushare + 因子守卫 + 配置面清除 → 测试 → dev 对账（翻转前置门）→ 生产上线（收盘后） |

## 设计

核心决策：

1. **守卫判据（后复权连续性）**：hfq[d-1] ≈ hfq[d] ⇒ `step = f[d]/f[d-1]−1 ≈ gap = close[d-1]/close[d]−1`。虚假/滞后调整 = 因子阶跃但价格无对应跳空 ⇒ step 与 gap 明显背离（可正可负）。注意计划文档把 step 写作 f[d-1]/f[d]−1 会恒反号误拒，按后复权连续性用 f[d]/f[d-1]−1。
2. **容差 `max(0.07, 0.5·|step|)`**：step−gap ≈ 除权日当日市场涨跌，小阶跃容差下限须吸收市场异动。dev 对账逐级调优：1.5% → 3% → 7%（曾误拒茅台 2008-06-16 step 0.57%/gap 3.45%、天齐 2013-06-13 gap 6.77%）。大阶跃按 ±50% 相对背离容忍（送转日 step≈gap 天然放行；平安/天齐 16%+ 仍拒）。
3. **比值对账二次校验（`rewrite_adj_factor.py --reconcile`）**：DB 既有因子 / BaoStock 派生因子应全史恒定（锚点抵消）+ **DB 因子自洽校验**（DB 因子阶跃须对应 DB 价格跳空，同 guard 判据跑在 DB 行上）。五类分类：
   - **接受**：guard 通过 + 比值 ≤0.5%，或无 DB 因子可比对（填补 NULL）→ 重写。
   - **守卫拒收（真异常）**：guard 拒收 + 比值漂移 >0.5% → 保留 Tushare（平安/天齐型）。
   - **守卫误伤（可重写）**：guard 拒收但比值一致 ≤0.5% → **纯大跌日无除权**（step≈0 被 7% 下限误伤，如 000009 2021-08-26 gap 8.4%、DB 因子仅动 0.1%）→ 两源一致，判定可重写。
   - **比值漂移（保留 Tushare）**：guard 通过但比值 >0.5% + DB 自洽 → 跨源口径分歧（000002 1.96%/600346 0.75%/601818 0.61%）→ 保留 DB 既有值。
   - **DB 侧异常（改写 BaoStock）**：guard 通过但比值 >0.5% + DB 因子不自洽 → DB 侧虚假调整（**601699 2026-08-26 Tushare 单日 glitch**：因子骤降 17% 价格反涨，08-27 恢复，prod 实证无真实除权）→ 判定改写 BaoStock。**教训**：比值漂移不天然等于「BaoStock 侧异常」，须以 DB 自身自洽性定位异常方。
4. **作用域 `--start 2016-01-01`**：dev/prod DB 复权因子覆盖均自 2016-08-01 起，2016 前无既有因子（NULL）；拉 1990+ 全史只引入远古异常拒收噪声（000001 1991/000002 1992/000009 1996）且耗时翻倍。
5. **BaoStock 免费源限频**：并发/突发过载触发黑名单（错误码 10001011「黑名单用户」）——dev 全量对账 8 分片并发（累积 ~500 查询/15min）实测触发。对策：全量重跑降并发（≤2）+ `--sleep 0.2-0.5` 逐股限频。生产 18:10 日任务为顺序逐股（~2h 窗口）不受影响。
6. **配置面**：maskToken 拆到 `service/mask.go`（llm.go 仍消费）；`004_delete_tushare_token.sql` 幂等删 token 行（migrate.sh 按序应用）。

## 实现

- `collector/app/collectors/{daily,index,valuation,calendar,finance,stock}.py`（改）：删 Tushare import 与分支；daily 删 `_fill_factor_cache`；兜底链 = BaoStock → AkShare。
- `collector/app/sources/tushare.py` + `collector/tests/test_tushare.py`（删）。
- `collector/app/sources/baostock.py`（改）：daily_pairs 空串→to_numeric 转 NaN（BaoStock 偶发空成交量，防 astype 崩溃）+ 无收盘坏条丢弃；模块 docstring 阶段 3。
- `collector/app/cleaners/factor_guard.py`（新）：除权一致性守卫，容差 `max(0.07, 0.5·|step|)`。
- `collector/app/collectors/tasks.py`（改）：`job_sync_daily_price` 简化单 per-stock loop；docstring 时序 09:00 列表/日历、18:00 财务、18:05 回填、18:10 行情、18:15 指数/估值。
- `backend/internal/service/config.go` + `backend/internal/api/handler/config.go`（删）；`backend/internal/service/mask.go`（新，恢复 maskToken）；`backend/internal/api/router.go`（改，删 3 条 /config/tushare 路由）。
- `deploy/postgres/init.sql`（改）：seed INSERT 删 `('tushare.token', ...)`；`deploy/migrations/004_delete_tushare_token.sql`（新）。
- `frontend/src/api/{settings,types}.ts` + `pages/Settings/index.tsx`（改）：删 Tushare 方法/类型/UI 区块；grid 改 repeat(2,1fr)。
- `scripts/rewrite_adj_factor.py`（改）：`--reconcile` 对账模式 + 比值二次校验 + `--sleep` 限频 + 单股拉取容错（连续 3 失败中止）+ `--start` 默认 2016-01-01。
- 测试：删 Tushare mock 用例；新增 factor_guard 三型用例 + daily_pairs 空串回归；**100 passed**。

## 验收

- [ ] **dev 对账（Step 7，翻转前置门）**：全池 800 只 → 拒收集 ≈ {000001, 002466}（平安/天齐真异常），误伤可重写 + 漂移保留 Tushare 分类明确，接受集比值 ≤0.5%。（⚠️ 过程记录：BaoStock 免费源并发过载触发黑名单 10001011，全量对账改为低并发 + `--sleep` 限频重跑）
- [ ] **生产上线（Step 8，收盘后）**：`build-release.sh` → `install.sh`；生产跑 `rewrite_adj_factor`（拒收/漂移股保留 Tushare）；确认 18:10/18:15 任务 success、data_quality 全绿、factor_value 6×800、两市成交口径不变。
- [ ] 抽验：茅台因子与旧 Tushare 恒比；平安/天齐因子未变。
- [ ] 回归：08-28 晚 7 项链路核对（阶段 2 待办）+ 本阶段核对。

## 遗留

- **601699 已定性为 DB 侧 glitch（改写 BaoStock，非保留）**——详见设计 §3「DB 侧异常」。
- **000002 万科比值漂移 1.96%、600346 0.75%、601818 0.61%** → 两源自洽、跨源口径分歧，保留 Tushare，量级小，接受尾部风险。后续可深挖具体分红/送转日差异。
- **守卫残余误伤面 = 纯大跌日（step≈0、gap>7%）**：不放大容差（放大会漏过真异常），由对账层比值兜底。
- **BaoStock 黑名单 10001011**：免费源突发/并发脆弱；全量/生产重写需限频（`--sleep` + 低并发），生产日任务顺序逐股不受影响。
- **08-28 生产封禁事故（dev/prod 双端 10001011，阻塞 Step 8 重写）**：dev 对账并发触发黑名单后，**prod IP 也连带被封**（匿名账号 `bs.login()` 无凭据共用，疑似账号级）。dev 封禁周期实测 ~5h（12:15→17:08 解除，17:10 恢复对账 2 分钟再复发）。**应对**：封禁期不锤；预计 ~22:10 恢复后按 runbook 单分片 `--sleep 1.5` 跑 rewrite（低于触发阈值）。**今晚 18:10 采集**：BaoStock 登录失败 → 逐股降级 AkShare（设计兜底，数据可落；每只多耗 ~4-8s 失败登录重试，800 只拖至 ~19:30-20:00；data_quality 18:30 可能见仍在跑为 warn 级可接受）。

## Step 8 rewrite runbook（封禁恢复后执行）

目标：adj_factor 全史切 BaoStock 派生，拒收/漂移股保留 DB（分类见 §设计3）。

```bash
# 1. 预检：确认封禁解除（单次 login+查询，不触发）
ssh quant@192.168.0.201 "docker exec quant-collector sh -c 'cd /app && timeout 60 python -c \"
from app.sources import baostock
sess=baostock.get_session()
raw,hfq=baostock.daily_pairs(sess, \\\"600519\\\", \\\"2026-08-25\\\", \\\"2026-08-27\\\")
print(len(raw))
sess.close()\"'"

# 2. 写入 rewrite：单分片、限频 --sleep 1.5（约 800 只 × ~2-4s ≈ 40-60 分钟）
#    （脚本已 docker cp 进容器；容器重启会丢，重新 cp：docker cp scripts/rewrite_adj_factor.py quant-collector:/app/scripts/）
ssh quant@192.168.0.201 "docker exec quant-collector sh -c 'cd /app && python scripts/rewrite_adj_factor.py --sleep 1.5'"

# 3. 期望分类（与 dev 对账一致）：
#    拒收（保留 DB）≈ {000001, 002466}（平安/天齐真异常）；比值漂移（保留 DB）≈ {000002,600346,601818}
#    DB 侧异常（改写）含 601699/601567/600460；其余接受/误伤改写

# 4. 验证
#    - 茅台 600519 因子与旧 Tushare 恒比；平安/天齐因子未变（拒收集）
#    - factor_value 当日 6 因子 × 800 齐全；data_quality 全绿
```

**限频纪律**：只用单分片；`--sleep ≥1.5`；封禁解除后首窗口若再触发（热封禁），立即停手等下一周期，勿连续锤。
