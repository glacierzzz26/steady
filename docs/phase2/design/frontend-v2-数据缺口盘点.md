# frontend-v2 数据缺口盘点

> 数据层面核实：frontend-v2 各页 mock 字段缺哪些、缺到什么地步、后台现有数据能否满足、不能满足的补救办法与是否收费。**已逐行核对 collector / quant-engine / backend 源码**（非凭接口契约想象），可作为接口契约 G1~G11 的数据可行性附录。
> 状态：📋 定稿（2026-08-23）· 关联：[接口契约](frontend-v2-api-contract.md) · [定稿索引](README.md) · [进度总表](../../进度总表.md)

## 1. 结论（先看这个）

1. **积分现实：Tushare 账号仅 120 积分**。`daily`/`adj_factor`/`trade_cal`/`stock_basic`/`index_daily`（120 分档）可用；`daily_basic`（估值 PE/PB）与 `fina_indicator`（财务 ROE）需 **2000 分档，当前不可用**。
2. **估值/财务靠采集器已内置的 AkShare 免费兜底** → **PE/PB/ROE 数据在库**。G1~G5、G7、G8 的后端数据**全部已产出或可从现有表推导**，缺的只是后端查询/组装代码 → **纯代码工作、零新增费用**。
3. **唯一真·未采集的是简报表（G6）3 个增强字段**：北向资金、两市成交、恒生/富时A50。补救**只能走 AkShare（无积分门槛）**，与现有 `hotspot.py` 纯 AkShare 采集架构一致。→ 前端先标「该能力未产出」空态，采集落地后自动点亮。

**零新增费用是硬结论**：全部补救路径要么是纯后端代码，要么走免费 AkShare（本项目行情主源 Tushare 只用到 120 分档免费接口）。

## 2. 积分与数据源现状（已核对 `collector/app/sources/tushare.py`、`collectors/valuation.py`、`collectors/finance.py`）

| Tushare 接口 | 用途 | 积分档 | 120 分可用 | 不可用时兜底 |
|---|---|---|---|---|
| `pro.daily` | 日线行情 | 120 | ✅ | —（主源） |
| `pro.adj_factor` | 复权因子 | 120 | ✅ | —（主源） |
| `pro.trade_cal` | 交易日历 | 120 | ✅ | — |
| `pro.stock_basic` | 股票列表 | 120 | ✅ | AkShare 交易所接口补 list_date |
| `pro.index_daily` | 指数日线 | 120 | ✅ | — |
| `pro.daily_basic` | 估值 PE/PB/市值 | 2000 | ❌ | **AkShare `stock_value_em`（东财估值，免费）** |
| `pro.fina_indicator` | 财务 ROE 等 | 2000 | ❌ | **AkShare `stock_yjbb_em`+`stock_zcfz_em`（东财业绩/资产负债表，免费）** |

> 数据血缘：`daily_valuation`（PE/PB/市值）与 `financial_indicator`（ROE/净利增速/营收增速/毛利率/负债率，含公告日）实际由 AkShare 兜底路径入库。新鲜度受逐股拉取速度影响，但不阻塞前端接入。

## 3. 逐缺口盘点（G1~G8；对应契约 §5）

| 缺口 | 缺什么字段 | 缺到什么地步 | 数据能否满足 | 补救办法 | 收费 |
|---|---|---|---|---|---|
| **G1** `/signals` 因子分项 | rank、趋势/价值/质量/风险、PE(TTM)、20日涨幅 | 接口缺字段，底层数据全有 | ✅ | `factor_value` 已存 6 因子 `normalized`（含每因子 rank）→ 四分类分项按权重组合现算；`rank` 从当日 score 横截面现排；`pe` 在 `daily_valuation.pe_ttm`；`chg20` 用 `daily_price`+`adj_factor` 前复权现算 | 否 |
| **G2** `/stocks` 评分池 | 最新价/涨跌幅/成交额、PE/PB/ROE、综合分/排名/信号 | 接口只回基础信息，底层数据全有 | ✅ | `daily_price`（价/涨/额）+`daily_valuation`（pe/pb）+`financial_indicator`（roe）+`strategy_signal`（score/signal，rank 现排），join 扩展 | 否 |
| **G3** 个股详情因子得分 | factor_score（综合分/排名/信号/四维雷达）、信号历史 rank | 接口缺字段，底层数据全有 | ✅ | 同 G1 数据源；`/stocks/:code` 财务 summary 已就绪 | 否 |
| **G4** orders/trades 名称 | `name` | 只缺一个 join | ✅ | `stock_basic.name` 就在，handler join 即可 | 否 |
| **G5** 数据健康检查 | 健康检查明细接口 | 接口缺，数据已每日产出 | ✅ | `data_quality.py` 7 项检查（覆盖/缺失日/重复/价格异常/估值/财务/基准）每日落 `task_run.detail`（含 `results`+`check_details`），只差只读 API 透出 | 否 |
| **G6** 早盘简报增强字段 | 北向资金、两市成交、恒生/富时A50（其余 sections 已产） | ⚠️ 3 个增强字段未采集 | 部分 | 见 §4 | 否 |
| **G7** 运维页 | 服务状态、数据资产行数 | 接口不存在；任务时间线已有 | ⚠️ 服务状态需进程/容器探活（基础设施代码非数据）；数据资产=DB 表 COUNT(*) | 后端加 `/health/services`（探活）+`/health/data-assets`（行数统计） | 否 |
| **G8** 回测 T+1 | fill_mode 与 T+1 统计 | 数据在（T+1 开盘价 qfq 在 `daily_price`） | ✅ | 引擎+接口扩展（契约 2.2 排期） | 否 |

## 4. G6 简报表拆解（已核对 `quant-engine/app/morning_brief.py`、`collector/app/collectors/hotspot.py`）

**已产，直接可用** ✅
- 隔夜外盘：美股 4 指（道琼斯/纳斯达克/标普500/纳指100）+ A股 3 指（上证/深成/创业板）——`market_hotspot.sections.indices`
- 热点板块（涨幅 `sectors_gain` + 资金流 `sectors_flow`）、活跃个股（人气榜/涨停池兜底 `hot_stocks`）
- 昨日回顾全节（信号/成交/净值/数据健康/任务）、今日计划清单、当前持仓

**未采集（前端 mock 有、数据真没有）** ❌ —— 补救全部走 **AkShare（免费、无积分门槛）**：

| 字段 | 现状 | AkShare 补救路径 |
|---|---|---|
| 北向资金（昨日） | 未采集 | 东财沪深港通资金流接口（如 `stock_hsgt_fund_flow_summary_em`；具体接口名实现时验证） |
| 两市成交（昨日） | 未采集 | 现货全市场快照（如 `stock_zh_a_spot_em`）当日 sum(成交额)；或全市场 `daily` 聚合 |
| 恒生指数 | 未采集 | AkShare 港指接口（如 `index_hk_stock_sina`；实现时验证） |
| 富时A50期指 | 未采集 | AkShare 期指/外盘接口（实现时验证） |

> 原则（契约 §5.1 G6）：collector 未产出的字段，前端先标「该能力未产出」空态，不造假数据；采集落地后自动点亮。上述 AkShare 接口均与现有 `hotspot.py` 纯 AkShare 架构一致，补数据成本低。

## 5. 对前端接入的约束（前端按此实现）

1. **缺口 ≠ 数据缺**：前端现在就按契约目标形态全量接入，缺字段标空态（数据诚实）；后端把 G1~G5/G7 查询落地后，页面**零返工自动点亮**。
2. **唯一真正空态是 G6 三个增强字段**（北向/两市成交/恒生A50），其余缺口均为「接口待扩展」。
3. **不花钱**：全部补救路径为纯后端代码或免费 AkShare；Tushare 只依赖 120 分档免费接口。
