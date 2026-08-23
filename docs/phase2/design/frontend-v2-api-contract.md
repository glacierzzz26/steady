# frontend-v2 接口契约

> 第二阶段执行蓝本。frontend-v2（14 页，纯 mock）是「产品愿景」，本契约定义每页数据需求对应的后端接口、现状、缺口与归属阶段，驱动 2.1~2.4 逐步实现。
> 状态：📋 定稿（2026-08-23）· 执行时按缺口归属阶段逐项落地，落地后勾选。
> 关联：[README](README.md) · [数据缺口盘点](frontend-v2-数据缺口盘点.md) · [进度总表](../../进度总表.md) · [项目详解](../../项目详解.md)

## 1. 背景与目标

frontend-v2 把系统定位从「数据监控 + 模拟交易」升级为「个人量化研究终端」。mock 页暴露的能力需求远超当前后端，本契约是「前端要什么 ↔ 后端有什么」的权威对照，也是分阶段补齐的依据。

**执行原则**：文档驱动——先按本契约补齐 2.1 缺口让 mock 接真实数据，再按 2.2/2.3/2.4 逐步补研究能力；一次不做完，每阶段落地后更新本表勾选。

## 2. 通用约定

- **响应信封**：`{code, message, data}`，`code=0` 成功；错误码 `40001 参数/40004 资源缺失/50001 内部`。
- **分页**：请求 `page`/`page_size`，响应 `{total, page, page_size, items}`。
- **日期**：`YYYY-MM-DD`（如 `2026-08-21`）；金额单位：元；涨跌幅/收益：百分比数值（前端加 `%`）。
- **信号 action**：后端 `BUY/SELL/HOLD`（大写），前端 `buy/sell/hold`（小写），接入层统一转换。
- **股票池 board**：后端 `universe`（`hs300`/`zz500`），前端 `hs`/`zz`，接入层映射。

## 3. 页面 → 接口映射总表

| # | 页面 | 数据需求 | 接口 | 现状 | 缺口 | 归属 |
|---|---|---|---|---|---|---|
| 1 | Settings | 数据源/通知/LLM 配置读写 | `/config/tushare`、`/notify/config*`、`/config/llm` | ✅ 完整 | — | 2.1 |
| 2 | Stocks | 股票池 + 最新价/涨跌/成交额 + PE/PB/ROE + 综合分/排名/信号 | `GET /stocks` | ⚠️ 只有基础信息 | G2 | 2.1 |
| 3 | StockDetail | 详情 + K线 + 财务 + 因子得分雷达 + 信号历史 | `/stocks/:code`、`/kline/:code`、`/stocks/:code/financial`、`/signals/:code` | ⚠️ 缺因子得分/信号/排名 | G3 | 2.1 |
| 4 | Signal | 信号明细 + 因子分项(趋势/价值/质量/风险) + 策略卡 | `GET /signals`、`GET /strategies` | ⚠️ 缺因子分项/rank/pe/chg20 | G1 | 2.1 |
| 5 | Trade | 账户卡 + 净值 + 持仓 + 委托 + 成交 | `/account`、`/account/nav`、`/positions`、`/orders`、`/trades` | ⚠️ orders/trades 缺名称 | G4 | 2.1 |
| 6 | Dash | 净值 vs 基准 + 持仓快照 + 今日信号 + 数据健康 | `/account/nav`、`/index/nav/:code`、`/positions`、`/signals`、数据健康 | ⚠️ 缺数据健康检查接口 | G5 | 2.1 |
| 7 | Brief | 早盘简报 + AI 解读 | `GET /morning-brief`、`POST /llm/interpret-brief` | ⚠️ sections 字段映射待核对 | G6 | 2.1 |
| 8 | Ops | 任务时间线 + 服务状态 + 数据资产概览 | `/tasks/runs`、`/health` | ⚠️ 缺服务状态/数据资产 | G7 | 2.1 |
| 9 | Backtest | 回测列表/详情/发起（T+1 成交假设） | `/backtests` | ⚠️ 缺 fill_mode 与 T+1 统计 | G8 | **2.2** |
| 10 | FactorLab | 因子 IC/ICIR/分层/衰减/相关性矩阵/配权 | — | ❌ 无接口 | G9 | 2.3 |
| 11 | FactorFactory | 因子 CRUD/版本/试算任务/参数寻优 | — | ❌ 无接口 | G10 | 2.3 |
| 12 | StrategyFactory | 策略 CRUD/版本/状态流转/A-B 对比/风控参数 | `GET /strategies`(只读 active) | ❌ 只读单例 | G11 | 2.4 |
| 13 | Live | Phase 3 概念（未实现） | — | — | — | P3 |
| 14 | Auth | Phase 3 概念（未实现） | — | — | — | P3 |

## 4. 现有接口契约（2.1 直接可用）

> 已核对 handler 与 DTO，以下为真实响应形状。

### 4.1 `GET /stocks` —— 股票列表（⚠️ 需扩展，见 G2）
```jsonc
{ "total": 801, "page": 1, "page_size": 20,
  "items": [{ "code":"000001","name":"平安银行","market":"SZ",
              "industry":"银行Ⅱ","list_date":"1991-04-03","status":"L","universe":"hs300" }] }
```

### 4.2 `GET /stocks/:code` —— 个股详情
```jsonc
{ "code":"000001","name":"平安银行","market":"SZ","industry":"银行Ⅱ",
  "list_date":"1991-04-03","status":"L","universe":"hs300",
  "latest_bar": { "date":"2026-08-21","open":..,"high":..,"low":..,"close":..,"volume":..,"amount":.. },
  "financial_summary": { "report_date":"..","announce_date":"..","pe":..,"pb":..,"roe":..,
                         "profit_growth":..,"revenue_growth":..,"debt_ratio":..,"gross_margin":.. },
  "valuation": { "trade_date":"..","pe_ttm":..,"pb":.. } }
```

### 4.3 `GET /kline/:code?period=day&adjust=&start=&end=` —— K线
```jsonc
{ "items": [{ "date":"..","open":..,"high":..,"low":..,"close":..,"volume":..,"amount":.. }] }
```

### 4.4 `GET /stocks/:code/financial?limit=` —— 财务列表
```jsonc
{ "items": [{ "report_date":"..","announce_date":"..","pe":..,"pb":..,"roe":..,
              "profit_growth":..,"revenue_growth":..,"debt_ratio":..,"gross_margin":.. }] }
```

### 4.5 `GET /signals?strategy=&date=&action=&page=&page_size=` —— 信号列表（⚠️ 需扩展，见 G1）
```jsonc
{ "strategy":"multi_factor","trade_date":"2026-08-21","total":28,"page":1,"page_size":100,
  "items": [{ "code":"601899","name":"紫金矿业","score":78.6,"action":"BUY","reason":"..." }] }
```

### 4.6 `GET /signals/:code?limit=` —— 个股信号历史（⚠️ 缺 rank，见 G3）
```jsonc
{ "code":"000792", "items": [{ "trade_date":"..","score":74.0,"action":"BUY","reason":".." }] }
```

### 4.7 `GET /strategies` —— 策略列表（只读 active，⚠️ 见 G11）
```jsonc
{ "items": [{ "name":"multi_factor","description":"..","factor_weights":{...},
              "params":{...},"status":"active" }] }
```

### 4.8 模拟交易：`/account`、`/account/nav`、`/positions`、`/orders`、`/trades`（⚠️ 见 G4）
```jsonc
// /account
{ "account_id":1,"name":"模拟盘","cash":97111,"market_value":2884,"total_asset":99995,
  "profit":-5,"profit_rate":-0.0001,"max_drawdown":0.042,"initial_cash":100000 }
// /account/nav?start=&end=
{ "items": [{ "trade_date":"..","total_asset":..,"nav":..,"daily_return":..,"drawdown":.. }] }
// /positions
{ "items": [{ "code":"000792","name":"盐湖股份","quantity":100,"available_qty":0,
              "cost_price":28.84,"current_price":28.84,"market_value":2884,
              "profit":0,"profit_rate":0 }] }
// /orders?status=&page=&page_size=
{ "items": [{ "order_id":"..","code":"000792","direction":"BUY","order_type":"..",
              "price":28.84,"quantity":100,"filled_qty":100,"avg_fill_price":28.84,
              "status":"filled","reason":"..","source":"..","created_at":".." }] }
// /trades?page=&page_size=
{ "items": [{ "trade_id":"..","order_id":"..","code":"000792","direction":"BUY","price":28.84,
              "quantity":100,"amount":2884,"commission":5,"tax":0,"net_amount":2879,
              "trade_date":".." }] }
```

### 4.9 `GET /index/nav/:code` —— 指数基准净值（Dash 对比用）
```jsonc
{ "code":"000300","items": [{ "trade_date":"..","nav":.. }] }
```

### 4.10 `GET /backtests?limit=`、`POST /backtests`、`GET /backtests/:id` —— 回测（⚠️ 见 G8）
```jsonc
{ "items": [{ "id":11,"strategy_name":"multi_factor","start_date":"2019-01-01","end_date":"2026-08-21",
              "top_n":20,"status":"done","error":"","created_at":"..","finished_at":"..",
              "total_return":0.382,"annualized_return":0.051,"max_drawdown":0.214,"sharpe":0.42,
              "trading_days":..,"final_value":..,"trades":..,"positions":..,
              "benchmark_return":..,"excess_return":..,"nav":[{ "date":"..","nav":..,"benchmark":null }] }] }
// POST /backtests 请求体（2.2 需加 fill_mode）
{ "start_date":"2019-01-01","end_date":"2026-08-21","top_n":20 }
```

### 4.11 `GET /morning-brief?date=` —— 早盘简报（⚠️ 见 G6）
```jsonc
{ "brief_date":"2026-08-24", "sections": { /* JSONB 结构见 §6 */ } }
```

### 4.12 配置类（2.1 直接可用）
- `GET/PUT /config/tushare`、`POST /config/tushare/test`
- `GET /notify/config`、`PUT /notify/config/:event`、`PUT /notify/config/feishu`、`POST /notify/test`
- `GET/PUT /config/llm`、`POST /config/llm/test`
- `GET /tasks/runs?limit=`（Ops 任务时间线数据源）

### 4.13 LLM（Brief AI / AI 助手）
- `POST /llm/interpret-brief` `{brief_date}` → 简报 AI 解读
- `POST /llm/glossary` `{term}`、`POST /llm/ask` `{question}`

## 5. 缺口清单与实现方案

### 5.1 第一阶段（2.1 前端接入）—— 先做

**G1 `GET /signals` 扩展因子分项与行情**（Signal 页）
- items 增加：`rank`（横截面排名）、`trend/value/quality/risk`（四因子分项分）、`pe`（TTM）、`chg20`（20日涨幅）。
- 数据来源：`strategy_signal` join `factor_value`（`factor_name` ∈ trend/value/quality/risk 族，取最近交易日）取分项；`rank` 可从 score 横截面排名或 factor_value.rank；`pe` 取 `daily_valuation`、`chg20` 取 `daily_price` 前复权 20 日涨幅。

**G2 `GET /stocks` 增加行情与评分信号**（Stocks 页）
- 方案：新增 `GET /stocks/pool`（或 `/stocks` 加 `with_score=1`），items 增加 `price/chg/amount/pe/pb/roe/score/rank/signal`。
- 数据来源：stock_basic join 最新 daily_price（price/chg/amount）+ daily_valuation（pe/pb）+ financial_indicator（roe）+ 最新 strategy_signal（score/rank/signal）。

**G3 个股详情 + 因子得分**（StockDetail 页）
- `/stocks/:code` 增加 `factor_score: {score, rank, signal, trend, value, quality, risk}`（雷达四维取自 G1 同源）。
- `/signals/:code` items 增加 `rank`。

**G4 `/orders`、`/trades` 补股票名称**（Trade 页委托/成交表）
- join `stock_basic.name`，items 增加 `name`。

**G5 数据健康检查接口**（Dash 数据健康、Brief 回顾）
- 新增 `GET /health/checks`，返回：
  ```jsonc
  { "items": [{ "name":"行情覆盖率","value":"99.8%","pct":0.998,"ok":true }, ...],
    "date":"2026-08-21" }
  ```
- 数据来源：复用 Iteration 2 数据健康检查（collector 已产数据健康结果表/日志），不足则先按现有检查项汇总。

**G6 简报表字段核对**（Brief 页）
- 核对 `morning-brief` sections JSON 与页面字段映射：`indices`→隔夜外盘、`sectors_gain`→热点板块、`hot_stocks`→活跃个股；北向资金/两市成交/昨日回顾/今日计划/被拒委托等字段，collector 未产出的先按「该能力未产出」标注空态，不造假数据。

**G7 运维页**（Ops 页）
- 服务状态：无现成接口。轻量方案：`GET /health/services` 返回各容器/进程状态（基于 docker inspect 或进程探活，VM 部署时改为按服务探活）。数据资产概览：基于 DB 表行数统计，新增 `GET /health/data-assets`。
- 任务时间线：`/tasks/runs` 已有，核对字段对齐。

**前端接入顺序（2.1）**：Settings → Stocks → StockDetail → Signal → Trade → Dash → Brief → Ops → Backtest；FactorLab/FactorFactory/StrategyFactory 保留 mock，标注「数据待 2.3/2.4」。**2026-08-23 已按此顺序完成 9 页接入**（逐页 `npm run build` 验证，见阶段归档 `../stages/frontend-v2-接入真实api.md`），G1~G8 以空态承接。

### 5.2 第二阶段（2.2 回测可信度校准，Iteration 3 ⭐）—— 研究地基

**G8 回测成交假设支持 T+1**
- `POST /backtests` 请求体增加 `fill_mode: "t_close" | "t1_open"`（默认 `t_close` 兼容现状，前端默认切到 `t1_open`）。
- 回测任务结果增加 `fill_mode`、`t1_deviation`（T vs T+1 年化偏差）、按 fill_mode 的指标组。
- 防未来函数验证：信号按 T 日生成、成交按 T+1 开盘价——引擎侧确保信号时间 <= 成交时间。
- 固定 fixture 边界测试：历史数据切片跑回测，结果可复现（同参同区间同 fill_mode → 结果一致）。
- 交付：T vs T+1 偏差报告，回测页展示「偏差」KPI。

### 5.3 第三阶段（2.3 因子研究闭环）

**G9 FactorLab 后端能力**
- `GET /factors` 因子定义列表（name/category/desc/formula/weight/status）——基于现有 `factor_definition` 表扩展版本/状态。
- 因子检验分析 API：`GET /factors/:name/stats` 返回 IC 序列、ICIR、分层收益、IC 衰减、相关性矩阵（数据来源：factor_value 历史 + 回测层）。
- 权重实验台：前端本地配权模拟，无需后端。

**G10 FactorFactory 后端能力**
- 因子 CRUD + 版本管理 + 状态流转（草稿→试算→检验→上线→停用）：`POST/PUT /factors`、`POST /factors/:name/versions`、`POST /factors/:name/trial`（异步试算任务）。
- 试算任务：`GET /factor-trials/:id` 返回 IC/ICIR/分层单调性结果。
- 参数寻优：`POST /factors/:name/optimize` 返回热力图数据。

### 5.4 第四阶段（2.4 策略生命周期 + 风控）

**G11 StrategyFactory 后端能力**
- 策略版本管理 + 状态流转（草稿→回测验证→样本外验证→运行中→归档）：`POST/PUT /strategies`、`POST /strategies/:name/versions`、`POST /strategies/:name/switch`（切换运行中策略）。
- 多策略并存，`strategy` 表扩展 `version/zh_name/status` 字段。
- A/B 对比：`GET /strategies/compare?name=&base_version=&candidate_version=` 同区间同假设对比。
- 风控参数落执行层：`strategy.params` 增加 `stop_loss`/`drawdown_fuse`，模拟盘执行时按持仓价与组合回撤触发止损/熔断；单票上限已存在于 config（max_position_pct）但需在策略 params 生效。

## 6. 早盘简报表 sections 结构（G6 核对用）

`market_hotspot.sections`（collector 产）：
```jsonc
{ "indices": [{ "name","code","close","change_pct" }],        // 隔夜外盘 + A股指数
  "sectors_gain": [{ "name","change_pct","leader" }],          // 板块涨幅榜 TOP_N
  "sectors_flow": [{ "name","net_inflow" }],                   // 板块资金净流入 TOP_N
  "hot_stocks": [{ "rank","code","name","change_pct" }] }      // 个股人气榜 TOP_N
```

## 7. 执行状态跟踪

> **2.1 前端接入已于 2026-08-23 完成**（Settings → Backtest 共 9 页接真实 API，`feat/frontend-v2-api` 分支）。缺字段一律按**数据诚实**原则标空态承接（不造假），后端补齐后前端**零返工自动点亮**。G1~G8 的**后端实现**属后续阶段（纯后端代码 + 免费 AkShare，见[数据缺口盘点](frontend-v2-数据缺口盘点.md)），不在 2.1 前端接入范围。
>
> **G1~G5/G7 后端实现已于 2026-08-23 按序落地并全链路验证通过**（见下表 ✅）；**G8 回测 T+1 已随 Iteration 3 于同日落地验证**（见下表 + 归档 `../stages/回测校准.md`）；**G11 策略生命周期+风控已随 Iteration 4 于同日落地验证**（见下表 + 归档 `../stages/策略与风控.md`）；G6 采集经可行性论证后维持空态（见 G6 采集可行性注记）。前端已接可选字段，零返工自动点亮。

| 缺口 | 归属 | 前端空态(2.1) | 后端实现 |
|---|---|---|---|
| G1 /signals 因子分项 | 2.1 | ✅ 列标「—」待 G1 | ✅ 2026-08-23：rank/trend/value/quality/risk/pe/chg20，735/735 排名与 reason 对账一致 |
| G2 /stocks 评分池 | 2.1 | ✅ 列标「—」待 G2 | ✅ 2026-08-23：price/chg/amount/pe/pb/roe/score/rank/signal，缺数据如实空态 |
| G3 详情因子得分 | 2.1 | ✅ 雷达「待 G3」 | ✅ 2026-08-23：factor_score（score/rank/signal/分项）+ /signals/:code rank |
| G4 orders/trades 名称 | 2.1 | ✅ name‖code 兜底 | ✅ 2026-08-23：join stock_basic 批量补 name |
| G5 数据健康检查 | 2.1 | ✅ 卡片「待 G5」 | ✅ 2026-08-23：/health/checks 7 项（含 pct 比例） |
| G6 简报表字段 | 2.1 | ✅ 字段映射核对通过（真实 sections 对齐）；北向/两市成交/恒生/A50「该能力未产出」 | ⬜ 采集已论证：见下方 G6 采集可行性注记 |
| G7 运维页 | 2.1 | ✅ backend/db 真实 + 其余灰显「待 G7」 | ✅ 2026-08-23：/health/services 6 服务探活 + /health/data-assets 21 表行数 |
| G8 回测 T+1 可信度 | 2.2 | ✅ 表单保留、提交不传 | ✅ 2026-08-23：fill_mode(t_close/t1_open)+t1_deviation，配对运行，11 个 fixture 用例，偏差报告 CLI，端到端验证通过（见归档 `../stages/回测校准.md`） |
| G9 因子检验分析 | 2.3 | —（页面保留 mock） | ⬜ |
| G10 因子 CRUD/试算/寻优 | 2.3 | —（页面保留 mock） | ⬜ |
| G11 策略生命周期+风控 | 2.4 | ✅ StrategyFactory/Backtest 已接通（真实 API） | ✅ 2026-08-23：策略 CRUD/状态机/单 active/fork、`/strategies/compare` A/B（按策略名，见归档偏差注记）、风控落 Go 实盘+Python 回测、turnover/cost。95 Go / 68 Python 全绿。归档 `../stages/策略与风控.md` |

> **G6 采集可行性注记（2026-08-23 论证，akshare 1.18.94 实测）**：G6 简报表 3 个增强字段中，**北向资金 2024+ 交易所已停止披露**（`stock_hsgt_hist_em` 近期全 NaN；`stock_hsgt_fund_flow_summary_em` 返回 0.0 不可信）；**富时A50 在 akshare 无任何接口**（源码全量 grep 仅无关 SGX 命中；`index_global_spot_em` 的 fs 列表无 A50）；**两市成交**东财源 `stock_zh_index_daily_em`/`stock_zh_a_spot_em` 含成交额列、生产环境可采，但本开发机东财域名直连+代理均被墙（ProxyError/RemoteDisconnected 3×重试全失败）；**恒生指数**新浪源 `stock_hk_index_spot_sina` 实测可取（HSI 26009.459/+1.210），但已归档前端 Brief 页无恒生展示位（top 行仅 A50/北向/两市成交 3 个 KPI，隔夜外盘卡片仅渲染美股）。→ 按数据诚实原则，G6 维持「该能力未产出」空态，不写无法在本机验证的采集代码；待生产环境/东财可达后再落地采集（届时前端已接可选字段、零返工点亮）。
