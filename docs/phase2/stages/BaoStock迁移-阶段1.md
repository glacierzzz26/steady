# 阶段 1：BaoStock 混合主源迁移（daily + calendar）

> 归档模板：每个阶段一页，按五节填写。定稿设计见 [`../design/数据源评估-BaoStock.md`](../design/数据源评估-BaoStock.md)（评估 08-26 完成，阶段 1/2/3 路径见 §5）。
> 关联：进度总表 `../../进度总表.md`（待办 · 数据源稳定性）· 数据缺口盘点 [`frontend-v2-数据缺口盘点.md`](../design/frontend-v2-数据缺口盘点.md)。
> 前置：本阶段是「数据源稳定性」待办的阶段 1，只切 daily + calendar 并对账，再放大阶段 2。

## 目标

「数据不稳定」的根因不是玄学，是**一处具体依赖被封锁**：东财 `82.push2.eastmoney.com` 双端 `RemoteDisconnected`，恰好命中三个核心采集器的主源——`daily`（退化新浪兜底）、`valuation`（**唯一源无兜底** → 东财一断 PE/PB/市值即缺）、`finance`（靠 Tushare 兜底，120/min 限额）。生产 60h 日志无报错 = 静默降级/缺数。

BaoStock（免费无 token、双端实测可达）作为 A 股日线核心新主源。**阶段 1 目标**：新增适配层，把 `daily` + `calendar` 切换为 BaoStock 主源（config 开关门控），dev 全量回填后与现有 Tushare 数据**逐位对账**，确认 OHLCV 可切换、复权因子风险暴露后再决定放大路径。

## 时间

| 起 | 止 | 说明 |
|---|---|---|
| 2026-08-26 | 2026-08-27 | 适配层 → dev 全池对账 → 限频重构 → 08-24 损坏修复 → 生产翻转 |

## 设计

核心决策（详见[定稿设计](../design/数据源评估-BaoStock.md)）：

1. **适配层「同形状输出」**（复刻 `sources/tushare.py` 模式）：上层 `build_rows`/`save`/清洗/重试**完全不动**，采集器只换主源，Tushare/AkShare 保留兜底。`get_session()` 恒可用（依赖缺失返回 None 时采集器跳过）。
2. **懒登录单例会话**：baostock 是自定义 TCP 协议、全局单 socket——`login()` 建连一次，逐只循环复用；socket 全程套超时，防库内 connect/recv 挂死。
3. **复权语义（8/26 用 600519 茅台 / 689009 九号实测确认，勿再踩）**：`adjustflag="1"` 后复权 = 与 Tushare adj_factor 同口径 → 派生因子必须用它；`="2"` 前复权最新日=原始价，派生因子全错（九号实测差 6.4%）；`="3"` 不复权（入库 close）。
4. **混合方案（全池对账后收窄，核心决策）**：OHLCV 全池**逐位一致**（close ±0.01 元、vol ±1 手、amount 相对差 0.01%）✅；但 **adj_factor 全池 51% 有分段阶跃**（含 000001 平安虚假调整 16.7%、002466 天齐配股滞后 15.8% 两个实证缺陷）❌ → **因子保留 Tushare 单一来源**，不切 BaoStock。生产翻转无需 k 归一化（原方案已弃用，乘常数 k 解决不了分段边界分歧）。
5. **因子按交易日批量 + 模块缓存**：`adj_factor` 免费档限频苛刻（**prod 实测 5次/天 + 1次/分钟、dev 1次/小时**），原逐股 `factor_map()` 在生产第一只就打爆配额、混合路径实际永远降级 AkShare。重构为按交易日批量：`factor_map_by_date(trade_date=)` 一次覆盖全市场，窗口内缺失日期逐日补模块级缓存（1 天 1 次调用），同一次同步后续股票全命中；多日期窗口逐日 **61s 错峰**（`_fill_factor_cache`）。
6. **孤立尖刺修复规则（08-24 事件）**：`f(D)` 与两侧邻日均不同才修；真实除权 `f(D)==f(D+1)` 天然跳过 → **universal-correct**（factor 在除权日之间恒定）。

## 实现

- `collector/app/sources/baostock.py`（新）：适配层——懒登录单例 + 失效重连、`daily_pairs`（OHLCV + 后复权）、`query_trade_dates`/`calendar`、socket 超时兜底（`edfcc91`）。
- `collector/app/collectors/daily.py`（改）：BaoStock 混合分支（OHLCV 走 BaoStock、因子走 Tushare，缺失整段降级）（`edfcc91`）；按日批量因子缓存 `_FACTOR_CACHE`/`_fill_factor_cache` + `factor_map_by_date` 接入（`6281965`）；多日期 61s 错峰（`16cd6e2`）。
- `collector/app/collectors/calendar.py`（改）：切 BaoStock 交易日历（`edfcc91`）。
- `collector/app/sources/tushare.py`（改）：新增 `factor_map_by_date`（`6281965`）。
- `collector/tests/test_daily.py`：3 例混合专项测试（OHLCV 走 BaoStock + 因子 Tushare 覆盖 + 缺源降级）+ 缓存复用 + 限频错峰 no-op fixture。
- `scripts/build-release.sh` 可执行位 + baostock 版本 pin 0.9.3（tsinghua 镜像无 0.9.30）（`ec7caee`）。
- 发布：`steady-20260827-6281965`（批量重构）→ `steady-20260827-66508db`（+61s 错峰 + 文档）生产部署，`BAOSTOCK_ENABLED=1`。

## 验收

- ✅ **dev 全池对账**（800 只 × 2016-08~2026-08 = 169 万行 vs 生产 Tushare）：close 超差 202 行全为 ±0.01 元舍入；vol 超差 1 行；amount 相对差 >0.01% 仅 6 行（**须用相对容差**——绝对值阈值误报 5.9 万行）；**adj_factor 51% 阶跃 → 混合方案**（见设计 4）。
- ✅ **Python 测试 77 passed**（含 3 例混合专项：`test_baostock_hybrid_factor_from_tushare` 断言因子被 Tushare 覆盖非 BaoStock 派生、`test_baostock_hybrid_factor_cache_reuse` 断言 3 日窗口仍只 3 次批量调用、`test_baostock_hybrid_no_tushare_falls_through` 断言缺源降级）。
- ✅ **生产 08-24 因子损坏修复**：孤立尖刺规则，prod 606+4 行 / dev 605+4 行；复核窗口内 >1% 尖刺归零、**dev/prod 因子逐位比对 14,392 行 0 偏差**。
- ✅ **生产翻转（66508db）**：`BAOSTOCK_ENABLED=1` 持久化，5 容器健康、`/api/v1/health` 200、collector 调度器无报错；BaoStock 可达（`get_session()` True、日历 569 天走 BaoStock）；降级链端到端正确（600519/000001/002466 close+adj_factor 与库内 Tushare 源逐位一致）。
- ⚠️ **混合腿（Tushare 因子成功返回）生产实时观测未完成**：验证窗口内 `adj_factor` 配额（1次/小时）已耗尽，prod/dev 两端都被限频挡住。但**代码路径已锁定**——部署镜像源码确认含批量循环+缓存+61s 错峰；生产日志实锤 fetch 进入混合分支并发起 `factor_map_by_date`（限频错误证明 token 已认证、代码已正确触达 Tushare）；77 测试锁定逻辑。后台探针监控在配额重置时自动补跑 `/app/verify.py` 出 PASS，无需人工介入。

## 遗留

1. **16:30 时序（用户决定不处理）**：BaoStock 当日数据约 18:00 后才出 → 16:30 日频增量实际仍走 Tushare/AkShare（OHLCV 逐位一致，无质量差）；混合路径真正吃 BaoStock 的是**多日缺口**（长假/复牌）与回填。若要 16:30 也走 BaoStock，需把日频同步挪到 18:30 后。
2. **阶段 2（下阶段接手）**：`valuation`/`finance`/`index`/`stock_basic`(industry 留 AkShare) 逐个切 BaoStock → 生产回填 + 上线；顺带 **G6 两市成交 = BaoStock `sum(amount)`**（进度总表待办）。
3. **新股/缺口全史回填**：>5 日窗口超 5次/天 配额 → 降级 Tushare/AkShare（可接受，数据仍正确）。
4. **阶段 3 开放项**：adj_factor 去 Tushare 依赖路径未决（设计 §6 ① 长期保留 / ② 第三复权源校验 / ③ 接受 ≤0.5% 边界差异全切）；`liabilityToAsset` 单位校准、`pe_static` 停采评估、北交所覆盖核对、2 年回填并行方案（设计 §6）。

## 补充（2026-08-27：生产验证暴露限频 + 08-24 因子损坏）

- **限频暴露与重构**：per-stock `factor_map()` 在生产 600519 命中 adj_factor 5次/天+1次/分钟 → 混合路径全退 AkShare。重构为按日批量 + 模块缓存 + 61s 错峰（`6281965`/`16cd6e2`）。
- **08-24 全市场因子损坏**：Tushare glitch——08-24 无任何真实分红（BaoStock 分红核对：茅台 06-26、平安 06-12、天齐无），但 606/799 只因子单向尖刺（74 只 >1%、最高 ±50%），08-25 全自愈；旧 Tushare snapshot 路径忠实落库。**孤立尖刺规则**修复 prod+dev，复核 dev/prod 0 偏差（见验收）。
