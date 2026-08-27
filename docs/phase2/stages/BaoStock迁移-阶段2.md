# 阶段 2：BaoStock 迁移（valuation / finance / index / stock_basic）+ G6 两市成交

> 归档模板：每个阶段一页，按五节填写。设计定稿见 [`../design/数据源评估-BaoStock.md`](../design/数据源评估-BaoStock.md)（§5 阶段 2/3 路径）。
> 关联：进度总表 `../../进度总表.md`（待办 · 数据源稳定性 阶段 2）· 阶段 1 归档 [`BaoStock迁移-阶段1.md`](BaoStock迁移-阶段1.md)。
> 前置：阶段 1 已切 daily+calendar，本阶段把 valuation/finance/index/stock_basic 逐个评估切换。

## 目标

延续阶段 1 的「数据源稳定性」：把 `valuation` / `finance` / `index` / `stock_basic` 四个采集器切到 BaoStock 主源（industry 留 AkShare），生产回填 + 上线，逐步摆脱 Tushare 依赖（阶段 3 才去 token）。顺带点亮 **G6 简报表「两市成交」**（`sh000001` 上证 + `sz399106` 深证综指 `daily_price.amount` 之和）。

**用户拍板（2026-08-27）**：上线节奏 = **两波灰度**（第一波 stock_basic+index，第二波 valuation+finance）；`daily_valuation` 的 `total_mv`/`float_mv`/`pe_static` **保留既有值**（BaoStock 分支只 upsert `close`/`pe_ttm`/`pb`，已核实三列无下游消费者）。

**实际执行比计划更保守**：dev 对账四个门里只有 `index` 全绿 → 生产仅翻转 `index` scope + G6；stock_basic（北交所缺口）、valuation（pb 口径差）、finance（roe 口径差）按「未通过则不硬切」规则**保留原源**。详见验收。

## 时间

| 起 | 止 | 说明 |
|---|---|---|
| 2026-08-27 | 2026-08-27 | 4 采集器适配 + 源级门控 + G6 → dev 对账（3 门失败定性）→ Wave 1 生产上线（index+G6）→ Decimal bug 实战修复 |

## 设计

核心决策（阶段 1「同形状输出」模式的自然延伸）：

1. **源级门控 `BAOSTOCK_SOURCES`**：config 新增 env 逗号列表，默认 `"daily,calendar"`（保阶段 1 生产行为）；`baostock_enabled(scope)` = `BAOSTOCK_ENABLED and scope in BAOSTOCK_SOURCES`。**生产翻转 = 在 .env 逐源追加 scope**（`daily,calendar` → `daily,calendar,index`），天然支持两波灰度；阶段 2 代码上线本身不改变生产数据路径。
2. **同日回退规则（valuation/index 通用）**：BaoStock 当日数据约 18:00 后才出 → 分支返回 `max(trade_date) < 请求 end_date` 即抛异常降级 Tushare。**16:15 指数 / 16:45 估值当日快照恒走 Tushare**（无滞后），历史/缺口/回填走 BaoStock——与阶段 1 daily 完全同构。
3. **finance 代码级门控**：BaoStock 财务接口按 code 逐期查、无全市场快照 → 分支仅在 `code` 提供时激活（per-stock 缺口/回填）；18:00 全市场日频任务本就走 AkShare，不依赖 Tushare。
4. **单位校准 ×100（实证修正计划 ×10000 的错误假设）**：BaoStock 财务返回小数比例（`liabilityToAsset=0.918356`、`roeAvg=0.179543`），库存百分数 → **×100**。dev 对账实证：600000 debt_ratio 0.918356×100=91.8356=库、600519 gross_margin 0.895552×100=89.5552=库，逐位一致。`revenue_growth` = 两期 `MBRevenue` 同比 ×100。
5. **北交所覆盖缺口（实测）**：`query_stock_basic` 返回 0 个 bj 码（前缀仅 sz/sh）→ **stock_basic 不切 BaoStock**（切了会丢北交所新股列表刷新与名称/状态新鲜度；存量行不删）。name 差 0.79% 全为 XD 前缀/全角Ａ美容差（BaoStock 名字反而更干净）。
6. **指数逐位一致（实证）**：sh000001 amount = 库逐位（952039262500.5）、close 舍入一致；BaoStock 指数 volume 股→÷100 手、amount 元原样（Tushare 千元→×1000 元，两源入库同 unit）。
7. **G6 两市成交（元）**：hotspot `_fetch_turnover` 读 `daily_price` 里 `sh000001`+`sz399106` 两行齐全的最近交易日求和 → `market.turnover`；透传 `assemble_brief` 的 `market` 节（零改动）→ 前端 `NoticeKpi` 渲染「两市成交(昨日)」。过渡期两指数缺尾 → 取两行都有的日期，绝不报半场数值；失败返回 None 优雅降级。
8. **指数代码映射**：`index_bs_code`（`sh000001`→`sh.000001`、`sz399106`→`sz.399106`）**不能复用 `bs_code`**（它把 000001 当股票映射成 `sz.000001`）；tushare `index_daily` ts_code 回退修复 `sz399106`→`399106.SZ`。

## 实现

- `collector/app/config.py`（改）：`BAOSTOCK_SOURCES` 源级门控 + `INDEX_CODES` 加 `sz399106`（`213d281`）。
- `collector/app/sources/baostock.py`（改）：新增 `stock_basic_rows`（type=1、`_plain_code` 剥前缀、ipoDate→list_date、status→L/D）、`valuation_rows`（`date,close,peTTM,pbMRQ`、adjustflag=3、不含 mv）、`index_bs_code`/`index_rows`（volume÷100、amount 元、默认 365 天窗口）、`financial_rows`（三接口拼装，**四比值 ×100 单位校准**）（`213d281`）。
- `collector/app/collectors/`：stock.py / finance.py / valuation.py / index.py 顶部加 BaoStock 分支（源级门控 + 同日回退）；valuation `save()` 按行含 `total_mv` 与否选 `update_cols`（保留既有市值/静态 PE）；index `INDEX_NAMES` 加 sz399106（`213d281`）。
- `collector/app/collectors/hotspot.py`（改）：`_fetch_turnover` 读两指数成交额 → G6（`213d281`）；**Decimal→float 修复**（DB numeric 经 SQLAlchemy 返回 Decimal，sections JSON 序列化崩，导致 hotspot 全链路 save 失败——生产实触发暴露，`e154728`）。
- `collector/app/sources/tushare.py`（改）：`index_daily_rows` ts_code 回退 `399106.SZ`（`213d281`）。
- `frontend/src/pages/Brief/index.tsx` + `api/types.ts`（改）：`NoticeKpi` 可选 value + `market.turnover` 渲染（`213d281`）。
- `collector/tests/`（改）：适配层形状/单位校准/同日回退/源级门控/Decimal 用例，**98 passed**（`213d281` + `e154728`）。
- `scripts/reconcile_phase2.py`（新）：dev 对账脚本——stock_basic/valuation/finance/index 四门 + 北交所覆盖核对（`213d281`）。
- 发布：`steady-20260827-213d281`（代码）→ `steady-20260827-e154728`（Decimal 修复）生产部署；`BAOSTOCK_SOURCES=daily,calendar,index`（Wave 1 翻转）。

## 验收

- ✅ **dev 对账**（`scripts/reconcile_phase2.py --sample 120`，dev 库，hs300+zz500 股票池）：
  - **index：✅ 通过**——close/amount 1012 行对比 0 差（逐位一致，amount 相对差 0）。
  - **valuation：⚠️ 通过 hard 门 / pb 软门失败**——close 31527 行逐位 0 差；pe_ttm ≤2% 31298/31527（99.3%）；**pb ≤2% 24488/31527（77.7%）**，诊断：偏差集中在财报披露边界（04-29 年报/Q1 季：Tushare 尚未采用新财报、BaoStock 更及时）+ 个别持续口径差（000408 自 07-01 恒 ~2.6%）。初判「未通过则不硬切」保留 Tushare；**后经用户拍板接受口径差翻转**（prod 对账 close 32007 行逐位 0 差，见遗留 1）。
  - **finance：❌ 脚本门未过**——debt_ratio 49/49 ✅、announce_date 49/49 ✅、revenue_growth 9/9 ✅；**roe 46/49 ≤0.5pp，3 行超**（000001 0.55pp / 000027 1.12pp / 000408 0.57pp，边界噪声非系统性 >1pp）。初判保留 AkShare（finance 日频本就走 AkShare，不依赖 Tushare）；**后经用户拍板翻转**（per-code 缺口/回填走 BaoStock，18:00 全市场仍 AkShare，见遗留 1）。
  - **stock_basic：❌ 未通过**——北交所覆盖 库 338 vs BaoStock 0；name 匹配率 99.21%（差项全为 XD 前缀/全角Ａ）、list_date 99.98%、market 100% → **保留 Tushare**。
- ✅ **生产 Wave 1 翻转（`steady-20260827-e154728`）**：`.env` 追加 `BAOSTOCK_SOURCES=daily,calendar,index` 并重启 collector（容器 env 实测确认）；`sync_index` 手动触发 True（BaoStock 主源 4 指数全同步）；**sz399106 全历史回填 8640 行（1991-04-04→2026-08-27）**，与 sh000001 对齐。
- ✅ **G6 两市成交数据落库**：`market_hotspot[08-27].sections.turnover = {sh: 1010226573954.6, sz: 1115700225234.22, total: 2125926799188.8, trade_date: 08-27}`（2.13 万亿）；`assemble_brief` 第 170 行 `"market": hotspot or {}` 原样透传 → 明早 09:10 组装 08-27 简报即点亮前端「两市成交(昨日)」。
- ✅ **Python 测试 98 passed**（含同日回退/源级门控/单位校准 ×100/Decimal 回归）。
- ✅ **生产健康**：collector 日志无错；task_run 全 success（data_quality 全部通过、consistency_check 对账通过）。
- ⚠️ **Decimal bug 实战暴露 + 修复**：生产触发 hotspot 暴露 `Decimal not JSON serializable`（sections 含 turnover 即 save 失败，整个 hotspot 采集回归）→ `float()` 转换 + Decimal 回归用例 → `e154728` 重建重发。教训：DB numeric 列经 SQLAlchemy 返回 Decimal，**对账/采集函数必须显式 float()**（单测用 Fake float 未覆盖）。

## 遗留

1. ~~Wave 2 待定~~ **已翻转（用户拍板 2026-08-27）**：`.env` `BAOSTOCK_SOURCES=daily,calendar,index,valuation,finance`，collector 重启。生产验证：600519 估值今日行 + total_mv/float_mv/pe_static 保留；财务 roe/debt 与 dev 逐位一致。**prod 对账门**：valuation close 32007 行逐位 0 差 ✅；finance 脚本门 ❌（roe 57/61 ≤0.5pp、4 行边界噪声 0.55-1.12pp，与 dev 同构——debt_ratio/announce_date/revenue_growth 全对）→ **用户知情后仍拍板翻转**（接受口径差：BaoStock 财报采用更及时，历史回填归一化到 BaoStock 值）。16:45 当日估值恒走 Tushare（同日回退）、18:00 财务全市场恒走 AkShare（code-gated）不变。
2. **stock_basic 保留 Tushare**：BaoStock 无北交所（338 vs 0），切了丢新股/状态刷新。若后续要全切，需为 bj 码保留 Tushare 或另寻源。
3. **BaoStock 指数历史深度**：`index_rows` 默认 365 天窗口（16:15 任务增量维护），sz399106 已一次性显式回填全史；新指数接入需同样显式回填一次。
4. **阶段 3 开放项**：adj_factor 去 Tushare 依赖路径未决；`liabilityToAsset` 单位校准已按 ×100 实证落地（计划 ×10000 假设作废）；`pe_static` 停采评估；16:30 时序（用户决定不处理）；东财封锁持续监控。

## 补充（2026-08-27：对账与生产实操修正）

- **单位校准计划假设 ×10000 是错的**：plan/对账脚本初稿沿用 `liabilityToAsset ×10000`（错误样本 0.009182×10000=91.82≈库被误读），实测 0.918356×100=91.8356 才逐位一致 → 全部按 **×100** 实现并写死防误用。debt_ratio/roe/gross_margin/profit_growth/revenue_growth 五字段统一 ×100。
- **北交所缺口实测**：`query_stock_basic` 全量返回 0 个 bj 前缀码 → stock_basic 对账门直接判不通过，计划 Wave 1（stock_basic+index）实际收窄为 index+G6。
- **hotspot Decimal 崩**：真 DB numeric→Decimal 撞 JSON 序列化，单测（Fake float）漏网；生产手动触发当场复现，修复 + 补 Decimal 用例后重发。
