# 数据接入层（datahub）设计：独立采集层 + MCP / HTTP 双出口

> **状态**：📋 设计（2026-10-08）｜**Phase 1 已实现**（2026-10-09，独立仓库 [glacierzzz26/datahub](https://github.com/glacierzzz26/datahub)）｜**类型**：跨仓库架构设计（本文以 steady 侧视角记录边界与契约；datahub 为**独立仓库/独立部署**，其服务级文档随仓库建立时另立）
> **体例**：沿用 [`数据源评估-BaoStock.md`](数据源评估-BaoStock.md) 的「现状 → 决策 → 边界 → 路径 → 风险」。
> **一句话**：把「对外部源的采集」从 steady 抽成一个独立服务 **datahub**（自有库、接管全部原始采集），对外经 **MCP + 原生 HTTP** 供数；steady 的 collector 退役，改为**按需调 datahub API** 取原始数据，本地只保留计算结果。

---

## 0. 决策记录（本轮拍板）

| # | 决策 | 选择 | 备注 |
|---|---|---|---|
| D1 | 消费方位置 | **跨机局域网** | 需 HTTP 传输 + token 鉴权（本生态首个鉴权面） |
| D2 | 仓库归属 | **独立仓库**（不再 monorepo） | datahub 自包含、独立版本/发布 |
| D3 | 部署拓扑 | **暂不定，先做边界** | 边界做干净后，部署位置/是否独立宿主机再议 |
| D4 | 采集范围 | **接管全部原始采集** | 行情/估值/财务/日历 + 热点/行业 |
| D5 | steady 取数方式 | **按需调 datahub API** | 单一真源，不落 steady 库 |
| D6 | 数据流方向 | **单向：datahub → steady/其他项目** | 本轮只供**原始数据**；计算产物（因子/信号）后续再议 |
| D7 | 出口形态 | **MCP + 原生 HTTP 双出口** | 层内 HTTP 契约为真实源，MCP 为薄门面 |
| D8 | 本轮交付 | **只出设计，暂不实现** | 实现分阶段另起 |

> 决策沿革：初版设想是「monorepo + 只读视图直连 steady 库」；随 D2/D4/D5 落定，架构改为「独立仓库 + 自有库 + 接管采集 + 按需 API」，**只读视图直连方案作废**。

---

## 1. 终态架构

```
        外部源（新浪/东财/同花顺/BaoStock/腾讯 …）
             │  采集（唯一出口 = datahub）
             ▼
  ┌───────────────────── datahub（独立仓库 / 独立部署 / 自有 DB）─────────────────────┐
  │  采集层  provider 注册表 + 每数据集源链 + 限流/缓存/降级/黑名单                    │
  │  存储层  原始数据（stock_basic/daily_price/daily_valuation/financial_indicator/    │
  │          trade_calendar/market_hotspot …）—— **单一真源**                          │
  │  出口层  MCP 门面(Streamable HTTP+token)   │   原生 HTTP API(+token)               │
  └──────────────┬───────────────────────────────┬───────────────────────────────────┘
                 │ 原始数据（按需 HTTP）           │ 原始数据（MCP/HTTP）
                 ▼                                ▼
        steady（collector 退役）              其他项目 / AI 客户端
        ├ quant-engine：取原始数据 → 算因子/信号
        ├ backend：取原始数据 → 供前端 API
        └ steady 自有 DB：**只存计算结果**
           （factor_value / strategy_signal / strategy_perf /
             account·position·order·trade·account_nav / backtest_* / task_run …）
```

**边界总表**

| 维度 | datahub | steady |
|---|---|---|
| 职责 | 采集 + 存储 + 供应**原始数据** | 计算 + 交易模拟 + 展示**计算产物** |
| DB | 自有库，含全部原始表 | 自有库，**只**含计算结果表 |
| 写谁 | 只写自己的原始表 | 只写计算结果表 |
| 对外 | MCP + HTTP（供 steady 与外部） | 前端 REST `/api/v1`（现状，仅改原始数据来源） |
| 依赖方向 | datahub ⇢ 外部源；datahub 不依赖 steady | steady → datahub（**单向**） |

**关键不变量**
1. **单一真源**：原始数据只在 datahub；steady 不存原始数据副本（可加只读**缓存**，见 §6.3，但缓存非权威）。
2. **单向依赖**：`steady → datahub`，datahub 绝不反向依赖/回调 steady。
3. **采集唯一出口**：任何对 A 股外部源的请求都必须走 datahub；steady 内不得再出现 `import akshare`/BaoStock/腾讯直调。
4. **本轮不含计算产物**：因子/信号/绩效/账户类数据不在 datahub 暴露范围内（D6）。若后续要供，另开「计算产物出口」设计（见 §10 未决项）。

---

## 2. 边界契约：数据集目录（Dataset Catalog）

这是**本设计的核心交付物**——两侧据此解耦开发。契约以「数据集」为单位，每个数据集冻结**参数、列名、类型、单位、语义**，并**版本化**。

### 2.1 契约原则

- **冻结列名/类型/顺序**：下游只认契约列，不认库内物理列。上游加列/改列必走契约版本（`v1 → v2`），下游显式升级。
- **单位显式**：金额单位（元）、成交量（手/股）、比率（% 或 0–1）在契约里写明，杜绝口径混淆（历史踩过 ×100/×10000 单位坑）。
- **防未来函数语义进契约**：财务类数据集带 `as_of` 参数（只返回 `announce_date ≤ as_of`）；估值类带 `asof` 回看窗口参数。
- **批量优先**：契约以「多码 × 区间」批量形态为主（对齐 steady 现有 `load_factor_inputs`/`load_range_inputs`/`preload` 批量取数），避免 N+1 往返。
- **统一信封**：HTTP 响应 `{code,message,data,meta}`（对齐 backend `pkg/response` 约定）；`meta` 含 `count/limit/offset/as_of/stale/source`。

### 2.2 数据集清单（对应 steady 消费面，已核实）

清单源自对 steady 现有读取点的实测梳理（quant-engine 12 文件 + backend repository/旁路）：

| 数据集 id | kind | 参数 | 关键列（冻结） | 供谁用 |
|---|---|---|---|---|
| `stock_basic` | raw | `universe`(hs300/zz500), `scope`(a_share/…), `market`, `industry`, `codes`, 分页 | code,name,market,industry,list_date,status,universe,data_scope | 池/名称/行业 |
| `trade_calendar` | raw | `start`,`end`,`is_open` | cal_date,is_open,exchange | 回测/就绪度/市场状态 |
| `daily_price` | raw | `codes`(多), `start`,`end`,`date`,`fields` | code,trade_date,open,high,low,close,volume,amount,adj_factor,turnover_rate | 因子/K线/回测/绩效 |
| `daily_valuation` | raw | `codes`,`asof`,`start`,`end` | code,trade_date,close,total_mv,float_mv,pe_ttm,pe_static,pb | 因子/展示/信号 |
| `financial_indicator` | raw | `codes`,`as_of`(防未来函数) | code,report_date,announce_date,pe,pb,roe,profit_growth,revenue_growth,debt_ratio,gross_margin | 因子/展示 |
| `latest_trade_date` | raw | — | trade_date | 编排/就绪度 |
| `market_ready` | raw | `date`,`threshold` | ready(bool),covered,total | 采集就绪闸门 |
| `market_hotspot` | raw | `date`(缺省最近) | spot_date, sections(json) | 早报/热点 |
| `index_quotes` | raw | `codes` | code,name,close,change_pct,trade_date | 前端指数条 |

对外（其他项目）另有**演示型**只读集（行情/估值/财务/日历/hotspot；不含因子/信号），与上表同源、按需裁剪。

> **说明**：因子/信号/绩效/账户等**计算产物不在本清单**（D6）。若外部也需，见 §10。

### 2.3 批量端点设计（对齐现有批量取数，防 N+1）

> **实现更正（2026-10-09）**：下表的「按资源分路径」（`/v1/calendar` 等）是**设计草图**；
> **实际实现为通用数据集端点** `GET /v1/datasets/{id}`（`{id}` = 数据集 id，如 `trade_calendar`），
> query 传参、统一信封 `{code,message,data,meta}`。数据集 id 与参数以 §2.2 契约代码为准
> （`datahub/app/datasets/`）。下表保留作**消费面映射意图**（每行对应 steady 哪处批量取数）。

| 端点（HTTP，v1） | 说明 | 对应 steady 现有函数 |
|---|---|---|
| `GET /v1/stocks` | 池/列表（批量） | `factor_service.pool_codes` / backend `stock_repo.GetList` |
| `GET /v1/prices?codes=...&start=&end=&fields=` | 多码区间行情 | `load_factor_inputs`、`replay.preload`、`_adj_series` |
| `GET /v1/valuations?codes=&asof=` | 多码估值（as-of） | `load_factor_inputs` 估值段 |
| `GET /v1/financials?codes=&as_of=` | 多码财务（防未来函数） | `load_factor_inputs` 财务段 |
| `GET /v1/datasets/trade_calendar?start=&end=&is_open=` | 交易日历（实现 id `trade_calendar`） | `replay.preload`、`engine._get_trading_dates`、`morning_brief`、`data_quality` |
| `GET /v1/latest-trade-date` | 最新交易日 | `tasks.latest_trade_date` |
| `GET /v1/hotspot?date=` | 热点 | `morning_brief` |

**MCP 门面**：每个数据集映射为一个 MCP tool（`get_prices` / `get_financials` …），inputSchema 由契约机械生成，返回 `data` 数组。**MCP 与 HTTP 调同一个内部 `get_dataset(id, params)`**——「薄门面」在代码结构上成立。

---

## 3. datahub 仓库职责（独立仓库）

```
datahub/                        （独立 git 仓库 / 独立 CI / 独立版本）
├── Dockerfile                  # python:3.12-slim（对齐现 collector 版本）
├── requirements.txt
├── app/
│   ├── config.py               # env 读取（DATAHUB_*）
│   ├── db.py                   # 自有库连接（可写，采集落库用）
│   ├── auth.py                 # Bearer token
│   ├── server.py               # 装配 FastAPI + 挂载 FastMCP + /healthz
│   ├── datasets/               # DatasetSpec 契约注册表（§2 的代码化）
│   ├── providers/              # 源适配 + 源链（复用 collector 现有取数函数，见 §4）
│   ├── cache.py / ratelimit.py # 能力层
│   ├── http_api.py / mcp_facade.py
│   └── collectors/             # 采集任务（从 steady collector 迁移，§7 Phase 2）
└── tests/
```

**datahub 从 steady 搬走的资产**（move，非 copy）：`collector/app/sources/*`（baostock/tencent/net）、`collector/app/collectors/*`（daily/valuation/finance/index/calendar/stock/hotspot 及 `scope`/`base`）、`collector/app/config.py` 的源门控范式、ADJ/守卫等。**校验与落库语义**（`guard_factor`/`cross_check_splits`/清洗）随采集一并搬到 datahub——因为落库与校验同属「采集」职责（与初版设想的「校验留 collector」相反：collector 退役后，校验自然归 datahub）。

---

## 4. 复用清单（避免重造）

从 steady 迁/借：
- `collector/app/collectors/daily.py:237 _PROFILES` + `:243` 源链分发 → datahub provider 注册表骨架。
- `collector/app/config.py:95 baostock_enabled` / `:127 tencent_enabled` / `:140 daily_source_chain` → datahub 闸门范式（沿用「双闸门默认关」）。
- `collector/app/collectors/base.py:34 with_timeout` / `:77 run`、`sources/net.py install_http_timeouts` → datahub 超时/重试（进程入口安装纪律）。
- `sources/tencent.py:61`/`baostock.py:67 is_source_blocked` + 冷却 → datahub 限流冷却。
- `collector/app/db.py` 单例引擎/`upsert` → datahub 库访问（保留写能力）。
- `quant-engine/app/factor_service.py:47 load_factor_inputs` / `factor_trial.py:57 load_range_inputs` / `backtest/replay.py:85 preload` → **API 契约的批量形态蓝本** + steady 侧 `data_source.py` 的接口骨架。

---

## 5. steady 侧改造（消费面）

### 5.1 quant-engine：新增 `app/data_source.py`（唯一切换点）
现状：多处裸查 SQL、无集中入口。改法——抽一个数据访问模块，提供批量读函数，替换内联 `select`：

```
load_prices(codes, start, end, fields) -> list[dict]              # 未实现（后续增量）
cal_dates(start, end, db=None) -> list[date]                      # ✅ 已实现（calendar）
is_open(d, db=None) -> bool                                        # ✅ 已实现（calendar）
recent_open_days(end, limit, db=None) -> list[date]               # ✅ 已实现（calendar）
latest_trade_date() / pool_codes(universe) / market_hotspot(date) # 未实现
```

**读闸门（零行为变更形态）** `DATAHUB_READ_DATASETS`（逗号白名单，**值 = dataset id**，如
`trade_calendar`；默认空 = 全读本地）：
- **闸门关**（默认）→ `data_source` 走**本地库**，SQL 与切换前**逐字一致**（保返回类型/语义）；
- **闸门开** → 走 `datahub_client`（**唯一出网点**：Bearer 鉴权 + 短 TTL 缓存 + 超时/重试）调
  `GET /v1/datasets/{id}`，本地库不查。
- **失败模式 = 失败即抛**（不静默回退本地）：datahub 是权威、本地是待退役副本，静默回退会掩盖故障。
  应急开 `DATAHUB_FALLBACK_LOCAL=1` 可回退本地（记 WARNING）。

**首个增量（2026-10-09）只落 calendar**，替换点：`morning_brief._is_open`、
`notify_scheduler._schedule_matches`、`watchdog.startup_catchup`、`backtest/engine._get_trading_dates`、
`backtest/replay.preload`、`data_quality._check_missing_days`；其余数据集随各自接管次序后续增补。
调用方仍用 `get_session()` 做**写**计算结果（`app/db.py` 保留）；`multi_factor.py` 无需改（只读计算结果）。

### 5.2 backend：抽「原始数据消费层」
现状：纯只读，集中于 repository，但有 3 处旁路（`service/market.go`、`service/morning_brief.go`、`handler/health.go`）+ 2 个混合端点（`/stocks`、`/signals`）。改法：
- 加一个 `datahub` 客户端包 + 在 repository 层把「读原始表」的实现替换为 HTTP 调用（保留函数签名，调用方不动）。
- **混合端点拆分**：`/stocks`、`/signals` 的原表片段（行情/估值/财务/名称）改走 datahub，计算表片段（`strategy_signal`/`factor_value`）留本地。
- **跨表原生 SQL 聚合重设计**：`GetPoolMarket`/`GetPoolValuation`/`GetPoolFinancial`/`GetSignalPe`/`GetSignalChg20` 在 API 化后改为「datahub 批量取 + 本地拼装」或「datahub 侧提供聚合端点」——**这是 backend 侧最大改造点**，需单独立契约。
- 纯原始端点（`/kline`、`/stocks/:code/financial`、`/index/*`、`/market/status`、`/morning-brief`）改走 datahub。
- **不动**：`/strategies*`、`/factors*`、`/performance/*`、`/account*`、`/orders`、`/trades`、`/backtests*`、`/tasks/*`（全读计算表）。

### 5.3 缓存策略（可选，不破单一真源）
steady 可在 `data_source.py` / backend 客户端内加**进程内只读缓存**（短 TTL，如当日行情落定后缓存）以减少往返、降低对 datahub 的依赖抖动。缓存是**性能优化**，非权威副本；datahub 始终是唯一真源。缓存 TTL 与采集时点错配时，用 `fresh=true` 绕缓存。

---

## 6. 能力层（datahub 内部）

- **缓存 TTL + 单飞**：外部实时集（热点/行业）TTL≈300s，单飞防并发打爆上游。
- **限流**：每 provider 信号量 + 最小间隔（沿用 `TENCENT_RATE_LIMIT` 语义）。
- **黑名单冷却**：`is_source_blocked` 命中 → 冷却期内不发请求。
- **降级**：源链左→右；全失败返 **stale + `meta.stale=true`**（有缓存）否则结构化错误——**绝不静默返回空**。
- **超时**：connect 5s / read 15s（沿用 `net.py`）。

---

## 7. 分阶段迁移路线（本设计的关键——拒绝 big-bang）

### 7.0 为什么必须灰度（"big-bang 风险"是什么）

**big-bang = 把所有改动一次性做完、全切过去**（对比"分阶段、一步步来"）。这里一把梭特别危险，原因是**现有 collector 里藏着一堆用生产事故换来的硬不变量**：

- `guard_factor` 除权守卫（拦 601155 型假缩股）
- 涨跌停 / 每手 / T+1 等成交口径
- 列漂移防护、upsert 用 None 清空既有值的坑（08-28 index 切源把指数成交额清 NULL）
- 源被限流的黑名单冷却（BaoStock 10001011 连带封禁）

这些**不是写代码时想出来的，是生产炸了之后补的**——回忆 08-28 那次，仅"index 换一个源"就花了几天才定位。若"接管全部采集 + 改 compute 取数"一次性做完：

1. **炸了分不清是谁炸的**：同时动了行情/估值/财务/日历/热点 5 条采集链 + 因子计算改取数，故障面上是一大团新代码，无法归因到单点。
2. **所有老不变量要同时重新验证**：踩坑补丁全要在新服务复现，一次性验完 = 赌自己没漏；漏一个就是重演一次事故。
3. **没法干净回滚**：全切后要退回，只能整坨回退，中间没有稳定点。

一句话：**big-bang 不是"不可能成功"，而是"一旦不成功，你失去定位和回滚的能力"**。在这套已多次吃数据源事故的系统上，这个代价过高——因此把"拒绝 big-bang"当**硬约束**，而非可选优化。

### 7.1 灰度原则（化解上面三点）

**逐数据集、闸门灰度、每步对账、每步可单独回滚**：一次只动一个数据集，切换前做「新服务落库 vs 旧 collector 落库」逐位对账零偏差，再切下一个 → 出问题范围极小、老不变量一次只重验一条、每步可用一把 env 闸门回退。

### Phase 0 — 边界与契约（本文）
- 冻结 §2 数据集契约、定义 §5.1 `data_source.py` / §5.2 消费层接口。**无代码。**
- **产出**：本设计文档 + 数据集契约清单。

### Phase 1 — datahub 起架 + 外部增强数据（先小后大）
- 建独立仓库；实现采集框架 + **只接「外部增强」数据集**（热点/行业）——这部分 steady 现在本就散在各 collector、无统一入口，风险低。
- 建 MCP + HTTP 双出口 + 鉴权。**Phase 1 不建自有库**——热点/行业「按需抓 + 缓存」取当前值即可，落库推迟到 Phase 2 接管核心采集时。
- **steady 零改动**（热点现由 collector 直采，暂并行；本阶段只验证「对外供数」链路）。
- **验收**：外部项目/ MCP 客户端能取到热点/行业；无鉴权 401。

### Phase 2 — datahub 接管核心原始采集（逐数据集灰度）
- 把 `daily/valuation/finance/index/calendar/stock` 采集器**逐个**从 collector 迁到 datahub（move + 校验/守卫一并迁）。
- 每个数据集一把 env 闸门（默认关 → 部署零行为变更）；**同一数据集在同一时刻只允许一家采集**（防双份）。
- steady 侧**先不动读取**，只验证 datahub 落库与 steady 落库**逐位一致**（对账脚本，沿用仓库既有对账范式）。
- **验收**：对账零偏差后逐数据集切换唯一采集方。

### Phase 3 — steady 改按需读取 + collector 退役
- quant-engine 用 `data_source.py` 切走判 API；backend 消费层切走 datahub。
- **逐数据集翻转**（先低风险 calendar → 估值/财务 → 最后行情，因行情涉 `guard_factor`）。
- 全部翻转且稳定一个完整交易日周期后，**删除 steady 内原始表读取代码与 collector 采集代码**。
- **验收**：`factor_value` 当日仍=800；信号/绩效与切换前一致；采集唯一出口=datahub。

### Phase 4（可选）— 本地原始表下线
- steady 库 drop 原始表，彻底单一真源。**保守可选**——可长期保留空表兜底。

---

## 8. 护栏（防新债）

1. **防双份采集**：每数据集单一采集方 + 闸门互斥；切换期用「datahub 请求数 ≈ steady 取数次数」比对验证。
2. **防环**：依赖单向（steady→datahub）；datahub 不 import steady、不回调。
3. **防列漂移**：契约冻结列 + 契约测试（对 datahub 侧 `information_schema` 断言）+ 版本化。
4. **鉴权**：Bearer token（`DATAHUB_TOKEN`，`.env` `chmod 600`）；nginx 必须 `proxy_set_header Authorization $http_authorization`（否则 401 假象）。
5. **只读对外 vs 可写采集**：datahub 内部要写原始表（采集），但**对外只读**——对外出口不含任何写操作；如需严格，对外可另用只读 DB 角色/只读 API 层。
6. **CI 守卫**：断言 steady 内不得出现外部源直调（`import akshare`/baostock/tencent），采集唯一出口=datahub。

---

## 9. 风险与取舍

| 风险 | 说明 | 缓解 |
|---|---|---|
| **compute 硬依赖 datahub** | 按需 API（D5）+ 独立部署 → 每日算因子的关键路径多一跳、新单点，datahub 挂→当天流水线停 | 部署拓扑待定（D3）：可同宿主降网络风险；datahub 自备 restart/healthcheck；steady 侧缓存 + 一键回退出账 |
| **big-bang 风险** | 接管全部采集 + 改 compute，一次性做=把踩坑不变量全重验 | §7 逐数据集灰度 + 闸门 + 对账 |
| **性能** | 重计算大范围拉数经 API 可能慢（回测全历史、因子全史） | 批量端点 + 分页 + steady 本地缓存；大回填走专用批量/导出通道 |
| **跨仓协调** | 契约/版本跨两仓，schema 变更需两侧对齐 | 契约版本化 + 契约测试 + 变更须两侧 PR 联动 |
| **安全** | 首个鉴权面；LAN 明文 token 可被嗅探 | token 管理规范；TLS 留后续（LAN 个人环境先接受） |
| **稳态运维复杂度** | 多一个仓库/服务/发布流水线 | 等独立诉求真实出现再拆（本设计即为 D2 的执行）；边界先做干净 |

---

## 10. 未决项（后续再议）

1. **计算产物出口**（D6 遗留）：外部是否也要因子/信号/绩效？是走 datahub 反向汇聚，还是 steady 自建出口？
2. **部署拓扑**（D3）：同宿主独立单元 vs 独立宿主机。
3. **二级缓存/跨重启缓存**：datahub 若要跨重启缓存，需自有可写 schema（与其「采集库」本就可写，冲突小）。
4. **TLS**：LAN 是否需要 nginx TLS + 自签。

---

## 11. 验证方法（实现阶段用）

1. **契约**：数据集契约测试——每个 `DatasetSpec` 的列对 datahub 侧 `information_schema` 断言一致；版本变更须显式。
2. **采集对账**：Phase 2 每数据集「datahub 落库 vs steady 落库」逐位对账零偏差。
3. **接缝验证**：Phase 3 翻转前后，同一交易日 `factor_value=800`、信号/绩效逐条一致。
4. **鉴权负例**：无 token → 401；错误 token → 401。
5. **无双份采集**：比对 datahub 请求计数与 steady 取数次数。
6. **MCP 冒烟**：MCP 客户端 `tools/list` 列全、调 `get_prices` 得数。

---

## 12. 文档同步清单

| 文档 | 改动 |
|---|---|
| 本文 `docs/phase2/design/数据接入层-datahub.md` | **新建**（定稿后登记 `design/README.md`） |
| `docs/phase2/design/README.md` | 登记一行 |
| `docs/进度总表.md` | 加「数据接入层 datahub 设计」行；各 Phase 落地后归档 `phase2/stages/` |
| `docs/系统手册.md` | 加 datahub 服务、双出口、鉴权；数据流骨架图加 datahub 分支 |
| `docs/项目详解.md` | 加「数据接入层」原理章 |
| `docs/llm/项目知识.md` | 补 datahub 服务/MCP/端口/token |
| `deploy/README.md` | 采集职责迁移、发布模型变化（若同宿主） |
| `docs/排障手册.md` | 加「MCP/HTTP 401」「datahub 连不上」「上游被封 stale 降级」「采集唯一出口切换」症状→处置 |
| **datahub 仓库** | 独立 README/service docs（该仓库建立时另立） |
