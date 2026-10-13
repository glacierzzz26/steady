# datahub Phase 3 · 读取切换（calendar 增量）

> 数据接入层 datahub 第三阶段：**steady 原始数据读取从本地库切到 datahub API**（按需 HTTP，单一真源）。
> 总设计 [`../design/数据接入层-datahub.md`](../design/数据接入层-datahub.md) §5；本阶段蓝图
> [`../design/数据接入层-datahub-phase3.md`](../design/数据接入层-datahub-phase3.md)。
> 承接 [`datahub-phase2-地基搬栈.md`](datahub-phase2-地基搬栈.md)（采集已切 datahub）。
> **生产操作手册**（底种 + 对账 + 翻闸 + 停采，只写不执行）：[`datahub-phase3-生产切换runbook.md`](datahub-phase3-生产切换runbook.md)。

**状态**：✅ **已全量生产落地**（2026-10-13）：`calendar` 2026-10-09 首切；`stock_basic`/`daily_valuation`/`financial_indicator`/`daily_price`（含 index）四集于 2026-10-13 生产翻读 + 停 steady 采集（qe + backend 双消费方），datahub 成**唯一采集方**。执行记录见 [`datahub-phase3-生产切换runbook.md`](datahub-phase3-生产切换runbook.md) §5。剩余：Tier3 读点本地陈旧待 Phase 4 重构、过渡期告警断链。

## 剩余数据集逐集全迁移（2026-10-09 起）

**范围**：把 `stock_basic / daily_price / daily_valuation / financial_indicator / index` 逐个从「steady 采 + steady 读」迁到「datahub 采 + steady 经 HTTP 读」。
**关键约束**：每个数据集的读消费方有两处（quant-engine + **backend Go**）；**停 steady 采集**是该数据集最后一步，须二者都已切读（否则 backend 服务陈旧数据）。
**每数据集范式**：① datahub 注册 raw 数据集 + 采集放闸 + 首灌 + **对账零偏差** → ② quant-engine `data_source.py` 增函数 + 改读点 + 翻闸 → ③ backend `internal/datasource` 增 accessor + 改 repo + 翻闸 → ④ 停 steady 采集（`COLLECTOR_DISABLED_JOBS`） → ⑤ 文档/归档。顺序 `stock_basic → index → valuation → finance → daily`（daily 最后，风险最高）。

**Increment 0（backend 基建，✅ 代码落地 2026-10-09，零行为变更）**：
- `internal/config`：`DatahubConfig` + `ReadEnabled`（`DATAHUB_*` env，与 quant-engine 同源）+ `getEnvList/getEnvBool/ParseDur`。
- `internal/datahub`（唯一出网点）：`FetchRaw`（Bearer/信封/瞬时重试/TTL 缓存/`Reset`）+ `Date` 适配器 + typed errors + 包级泛型 `Fetch[T]`。
- `internal/datasource`（唯一切换点）：`Source`（`Enabled/FallbackLocal`）+ stock_basic/calendar/daily/valuation/financial accessor 与 wire DTO。
- 测试：`client_test.go`（httptest）+ `config_test.go` + `source_test.go`；`go test ./...` 全绿。
- **默认 `read_datasets: []` + token 空 ⇒ 恒 disabled ⇒ 部署零行为变更**。repo 方法切换 + 接线随各数据集增量推进（Tier1/2/3 分级见设计 §6）。

## 目标

把 quant-engine 的原始数据读取从「裸查本地表」改为集中入口 `app/data_source.py`，按 env 闸门走
datahub HTTP。**首个增量 = calendar**（`trade_calendar` 已由 datahub 采），为后续数据集验证整套读切换机制。
闸门默认关 → 部署零行为变更；本地库不删、collector 不删（作回退）。

## 时间

| 起 | 止 | 说明 |
|---|---|---|
| 2026-10-09 | 2026-10-09 | 代码/测试 + 放行门 + 生产发布翻闸同日完成 |

## 设计

- **读闸门 `DATAHUB_READ_DATASETS`**（逗号白名单，值 = dataset id；默认空 = 全读本地）。判定 =
  `base_url 非空 × token 非空 × dataset ∈ 白名单`。回退 = 清空该值重启。
- **唯一出网点** `app/datahub_client.py`：Bearer 鉴权、信封解析、重试只对瞬时故障（超时/5xx）、
  4xx 快速失败、显式 timeout、进程级单例 + 短 TTL 缓存。**不装 `requests.Session` 全局补丁**
  （遵 `sources/net.py`「仅 `__main__` 安装」纪律）。
- **唯一切换点** `app/data_source.py`：闸门关 → 本地 SQL（与切换前逐字一致）；闸门开 → datahub。
- **失败模式 = 失败即抛**（不静默回退本地）：datahub 权威、本地待退役；静默回退掩盖故障。
  应急开 `DATAHUB_FALLBACK_LOCAL=1`（记 WARNING）。
- **前置硬约束**：datahub `trade_calendar` 覆盖须 ⊇ steady（否则回测截断）——见下。

## 实现

quant-engine（分支 `feature/datahub-read-switch`）：

- `app/config.py`（新）：env 助手（函数形式，调用时读）+ `datahub_read_enabled(dataset)`。
- `app/datahub_client.py`（新）：HTTP 客户端 + typed 异常（`DatahubHTTPError/Timeout/ParseError`）。
- `app/data_source.py`（新）：`cal_dates`/`is_open`/`recent_open_days` 三函数。
- 改 R1–R6 读点：`morning_brief._is_open`、`notify_scheduler._schedule_matches`、
  `watchdog.startup_catchup`、`backtest/engine._get_trading_dates`、`backtest/replay.preload`、
  `data_quality._check_missing_days`；各点移除未用的 `TradeCalendar` import；写路径不动。
- `requirements.txt` 增 `requests`。
- 测试：`tests/conftest.py`（autouse 清 `DATAHUB_*` + 重置单例）、`tests/test_datahub_client.py`
  （fake session）、`tests/test_data_source.py`（闸门关/开 + 放宽窗口 + 失败即抛/回退）。
  **全量 `pytest` 181 passed。**

部署接线：`deploy/.env.example` 增 `DATAHUB_*`（并修正「仅数据库凭据」措辞——`DATAHUB_TOKEN` 是
第二处密钥）；两 compose 已 `env_file: .env`，无需改。

## 放行门（✅ 已过，2026-10-09 生产）

1. ✅ **datahub 全史补录**：以 env 覆盖强制走 AkShare 全史源
   （`docker exec -e BAOSTOCK_SOURCES=daily,index,valuation,finance <collector> python -m app.cli sync-calendar`
   → `baostock_enabled("calendar")` 为 False → `ak.tool_trade_date_hist_sina()`，幂等 upsert）。
   **结果**：`datahub.trade_calendar` 545 → **8797 行**（1990-12-19 → 2026-12-31）；`≤today` 行数
   8738 = steady 8738（**逐位一致**）。
2. ✅ **全区间对账零偏差**：`reconcile_calendar.py --all`（PR glacierzzz26/datahub#10，脚本经 stdin
   投入容器运行）→ `datahub=8738 steady=8738 锚点=2026-10-09`，**accepted 8738 / 其余 0，退出码 0**。

## 生产发布与翻闸（✅ 2026-10-09）

- **发布**：`steady-20261009-87e7dc7`（dev→master PR #37 → `scripts/build-release.sh` → `install.sh`）。
  版本号 `v0.0.0`（非正式发版）。
- **零行为验证**：`install.sh` 复用旧 `.env`（md5 `ba7107dd…` 前后一致）→ 停采闸门
  `COLLECTOR_DISABLED_JOBS=job_sync_calendar` 保留；未加 `DATAHUB_*` 时读路径仍本地。
- **翻闸**：生产 `.env` 加 `DATAHUB_*`（白名单**先空**，验闸门关=读本地）→ 置
  `DATAHUB_READ_DATASETS=trade_calendar` → `--force-recreate quant-engine`（**不动 collector**）。
- **翻闸后验证**：
  - 闸门开：`config.datahub_read_enabled("trade_calendar") == True`；datahub 可达（`/v1/healthz` 200）。
  - **逐位一致**：`cal_dates("2026-08-01","2026-10-09")`=44（08-03→10-09）、
    `recent_open_days(2026-10-09,10)`、`is_open(2026-10-09)=True` 与本地基线**完全相同**。
  - **全史**：`cal_dates("1990-01-01","1995-12-31")`=1279 行（回测网格完整）；2026 全年=242。
  - **真 HTTP**：datahub 访问日志 `GET /v1/datasets/trade_calendar?... 200 OK（源 db）`（来自 quant-engine）。
  - **端到端**：`job_data_quality()`（R6 经 datahub）→「数据健康检查完成 2026-10-08：全部通过」。
- **回退**：生产 `~/steady-20261009-87e7dc7/.env` 加键前已备份为 `.env.pre-readgate-20261009`；
  一键回退 = 清空 `DATAHUB_READ_DATASETS` 重启 quant-engine。

## 待办

- 观察一个完整交易日周期（次日各 job 日志无异常、结果一致）。
- 后续数据集 `stock_basic → index → valuation → finance → daily` 逐个增补到 `data_source.py` 并翻闸。

## 验证（已做）

- ✅ `pytest tests/` **181 passed**（含新增 20）。
- ✅ **放行门**：datahub 全史补录 + 全区间对账零偏差。
- ✅ 生产端到端（发布 + 翻闸 + 逐位一致 + 全史 + 真 HTTP + job 冒烟）。

## ⚠️ 发现的既有缺口（非本改动引入）

- **镜像未烘焙 `APP_VERSION`/`GIT_SHORT`**：`docker exec quant-engine sh -c "echo \$APP_VERSION \$GIT_SHORT"`
  为空；run compose 与 Dockerfile 均未设置。与全局发布纪律（「镜像内烘焙版本号 + 短 hash」）不符，
  建议后续单独修复。

## stock_basic 读切换（qe 侧，Increment 1 步骤②，2026-10-09）

首个「**非日历**」数据集（有本地表、steady 仍在采、backend 也读）——验证读切换在有真实
数据面的完整通路。**闸门默认关**：不改 `.env` 则逐字回退本地。

- `app/data_source.py` 增：`pool_codes`（策略池，`universe∈hs300/zz500`，升序）、`names_by_codes`、
  `industries_by_codes`、`a_share_listed_codes`（coverage 分母，`status='L'×list_date<=td`）、
  `a_share_listed_count`（分母漂移守卫真值）、`pool_code_dates`/`a_share_code_dates`（financial 覆盖分母）。
- 切读点：`factor_service.pool_codes`（连带 factor_trial/factor_research/performance）、
  `tasks.market_ready`、`data_quality._coverage_pool` + 分母真值 + `_check_financial` 分母、
  `morning_brief._positions_section`（把 `outerjoin StockBasic` 拆为「本地持仓 + `names_by_codes`」）、
  `notify_scheduler._code_names`、`backtest/replay.preload`（池 + industry）。
- **语义**：datahub `stock_basic` 无 `status`/`list_date` 服务端过滤 → 本侧拉小表（~5.5k 行）后
  在 Python 过滤（NULL 一律排除，对齐本地 SQL `status='L' AND list_date<=td`）；
  各读点移除不再使用的 `StockBasic` import。
- 测试：`tests/test_data_source.py` 增 stock_basic 本地/远端用例（池/名称/行业/coverage 分母/失败即抛/回退）；
  **全量 `pytest` 195 passed**。
- 放行门：datahub 首灌 `stock_basic` + 与 steady 逐位对账零偏差（**待生产执行**，见 datahub PR）。

## stock_basic 读切换（backend Go 侧，Increment 1 步骤③，2026-10-09）

承接 qe 侧（步骤②）与 backend 基建（Increment 0）。把 `StockRepository` 的全部 stock_basic
读点从「裸查本地表」切到「闸门开走 datahub、闸门关逐字本地」。**闸门默认关 = 部署零行为变更**。

- `internal/datasource`（唯一切换点）增列表 accessor：`StockBasicFilter`（`Codes/Industry/Keyword/
  Market/Universe/Scope/Sort/Order`，不含分页）+ `StockBasicList`——服务端过滤+排序（对齐 datahub
  dataset 参数），**不下发 limit/offset**（分页/总数由调用方做，否则 total 失真）。
- `internal/repository/stock_repo.go`：`StockRepository` 加 `ds *datasource.Source`；构造函数
  **变参可选** `NewStockRepository(db, ds ...*datasource.Source)`（现有 `NewStockRepository(tx)`
  tx-scoped 调用点零改动 ⇒ nil ⇒ 纯本地，保事务内一致）。四个读点加 prologue：`GetList`
  （映射 `StockBasicFilter` → Go 侧 `total=len()` + `(page-1)*pageSize` 切片，排序 NULLS LAST
  与本地 `stockSortClause` 一致）、`GetByCode`/`Exists`（`StockBasicByCode`，命中即 true）、
  `GetNames`（`StockBasicByCodes`）。**失败即抛**（默认；`fallback_local=1` 记
  WARNING 后落本地）。
- 接线：`cmd/server/main.go` 构造 `datasource.New(cfg.Datahub)` → `SetupRouter(db, ds, …)` +
  `NewTradingService(db, cfg.Account, ds)`（`SetupRouter` 首参新增 `ds`）。
- 测试：`internal/datasource/source_test.go` 增 `StockBasicList`（参数透传/不下发 limit/offset）；
  新增 `internal/repository/stock_repo_ds_test.go`（httptest 假 datahub，`db=nil` ⇒ 走 ds 时不触库），
  覆盖 `GetList`（分页切片/total/越界页/参数透传）、`GetByCode`（命中/未命中）、`Exists`、`GetNames`、
  及「datahub 500 ⇒ 失败即抛」。**全量 `go test ./...` 全绿**（api 集成测试无 `TEST_DB_DSN` 自动 skip）。
- 放行门（同 qe 侧）：datahub `stock_basic` 首灌 + 逐位对账零偏差，**待生产执行**。

## daily_valuation 读切换（qe + backend，2026-10-12）

首个「qe + backend 双消费方**同批**切」的数据集（calendar/stock_basic 均为逐消费方增量）。
**闸门默认关 = 逐字回退本地**。

**datahub 侧**：注册 raw 数据集 `daily_valuation`（`db` provider 读自有库；列
`code,trade_date,close,total_mv,float_mv,pe_ttm,pe_static,pb`；params `codes` 逗号 IN /
`start`/`end` 区间 / `asof`(≤) / `last`(每码最近 N 行，服务端 `ROW_NUMBER`) / `fields` 投影）。
见 datahub 仓 PR。

**qe 侧**（`quant-engine`）：
- `app/data_source.py` 增：`valuation_rows(db, codes, start, end, fields)`（闸门关本地 SQL 逐字、
  闸门开走 `daily_valuation`；远端按 `fields` 投影为**有序元组**，调用方 `for code,td,pe,pb` 几乎不改）、
  `valuation_latest(db)`（走 `latest_trade_date{table:daily_valuation,exclude_index:false}`）、
  `dup_keys(db, table)`（走 `dup_keys{table}`）。
- 切读点：`factor_service.load_factor_inputs`（估值段）、`factor_trial.load_range_inputs`（估值段）、
  `data_quality._check_valuation`（`valuation_latest`）+ `_check_duplicates`（daily_valuation 走 `dup_keys`，
  其余三表仍本地 `_dup_count`）、`backtest/replay.preload`（估值 as-of 段）；各移除不再使用的
  `DailyValuation` import。
- 测试：`tests/test_data_source.py` 增 valuation 用例（rows 投影/空码短路/latest 命中与空/`dup_keys`/
  失败即抛/回退）；**全量 `pytest` 205 passed**。

**backend（Go）侧**：
- `internal/datasource` 增 accessor `ValuationLast(ctx, codes, n, asof)`（`daily_valuation` params
  `codes`/`last`/`asof`；服务端每码最近 N 行、升序返回）——把 Tier2 聚合变**有界批量**，一次往返无 N+1。
- `DailyRepository` 加 `ds *datasource.Source` + 变参构造 `NewDailyRepository(db, ds...)`：
  `GetLatestValuation` prologue → `ValuationLast([]string{code},1,nil)`（无行 → nil）。
- `SignalRepository` 加 `ds` + 变参构造：`GetSignalPe` prologue → `ValuationLast(codes,1,&date)`
  （每码 `trade_date ≤ 信号日` 最新一行），`pe_ttm≠0` 过滤（对齐原 SQL `pe_ttm IS NOT NULL`）。
- `StockRepository.GetPoolValuation` prologue → `ValuationLast(codes,1,nil)`，`pe/pb` 全空滤除
  （对齐原 SQL `pe_ttm IS NOT NULL OR pb IS NOT NULL`）。
- 接线：`SetupRouter`（`dailyRepo`/`signalRepo`）与 `cmd/server/main.go`（`dailyRepo`）注入 `ds`；
  `NewTradingService` 的**非 tx** `dailyRepo`/`signalRepo` 注入 `ds...`；**tx-scoped**
  （`trading.go` `ExecuteDay` 内 new 的 repo）一律不传 = nil（保事务内一致）。
- **NULL 语义**：datahub 估值列 NULL 经 wire DTO 归 0（`model.DailyValuation` 为 float64），
  故以「≠0」充当「非空」判定——PE/PB 真实数据恒不恰为 0，无信息损失。
- 测试：新增 `internal/repository/valuation_ds_test.go`（httptest 假 datahub，`db=nil` ⇒ 走 ds 不触库）：
  `GetLatestValuation`（取最新/参数透传 `last=1`/未命中 nil）、`GetSignalPe`（`last=1`+`asof`/null PE 滤除）、
  `GetPoolValuation`（pe 空 pb 有值保留、全空滤除）、「datahub 500 ⇒ 失败即抛」。
  **`go test ./...` 全绿**（`internal/api/handler` 的 `TestHTTPProbe/连接被拒_down` 为**既有偶发**
  ——干净树 `count=1` 复现，与本次改动无关）。
- 放行门（datahub 首灌 `daily_valuation` + 逐位对账零偏差）与停采 **待生产执行**。

## financial_indicator 读切换（qe + backend，2026-10-12）

第二个「qe + backend 同批切」的数据集（范式同 daily_valuation）。**闸门默认关 = 逐字回退本地**。

**datahub 侧**：注册 raw 数据集 `financial_indicator`（`db` provider 读自有库；列
`code,report_date,announce_date,pe,pb,roe,profit_growth,revenue_growth,debt_ratio,gross_margin`；
params `codes` 逗号 IN / `as_of`(announce_date ≤) / `last`(每码最近 N 期，服务端
`ROW_NUMBER OVER (PARTITION BY code ORDER BY announce_date DESC, report_date DESC)`) / `fields` 投影）。
见 datahub 仓 PR。**注**：`announce_date` 为 `NOT NULL`（schema），故无 NULLS FIRST 风险，原
「`announce_date IS NOT NULL` 前置过滤」为无操作，datahub 侧不需要 `announce_not_null` 参数。

**qe 侧**（`quant-engine`）：
- `app/data_source.py` 增：`financial_rows(db, codes, as_of, fields)`（闸门关本地 SQL 逐字、闸门开走
  `financial_indicator`；远端按 `fields` 投影为**有序元组**，调用方 `for code,rd,ad,roe,debt` 几乎不改）、
  `pool_latest_announce(db, codes)`（走 `financial_indicator {last:1,fields:code,announce_date}`；
  闸门关 = `GROUP BY code MAX(announce_date)`）。
- 切读点：`factor_service.load_factor_inputs`（财务段）、`factor_trial.load_range_inputs`（财务段）、
  `backtest/replay.preload`（财务 as-of 段）、`data_quality._check_financial`（`pool_latest_announce`）；
  各移除不再使用的 `FinancialIndicator` import（`data_quality._check_duplicates` 仍本地 `_dup_count`，
  故该文件保留 `FinancialIndicator` import）。
- 测试：`tests/test_data_source.py` 增财务用例（rows 投影/空码短路/`as_of` 透传/latest announce 命中与空/
  失败即抛/回退）；**全量 `pytest` 212 passed**。

**backend（Go）侧**：
- `internal/datasource` 增 accessor `FinancialLast(ctx, codes, asOf, n)`（`financial_indicator` params
  `codes`/`as_of`/`last`；服务端每码最近 N 期、按 `report_date ASC` 返回）——Tier2 聚合变**有界批量**。
- `FinancialRepository` 加 `ds *datasource.Source` + 变参构造 `NewFinancialRepository(db, ds...)`：
  `GetByCode` prologue → `FinancialByCodes([]string{code})`（datahub 升序 → Go 侧按 `report_date DESC`
  排序 + `limit` 截断，复现本地 `ORDER BY report_date DESC LIMIT n`）、`GetLatestByAnnounce` prologue →
  `FinancialLast([]string{code},nil,1)`（`last=1` → 每码恰一行即最新；无行 → nil）。
- `StockRepository.GetPoolFinancial` prologue → `FinancialLast(codes,nil,1)`，`roe==0` 滤除
  （datahub NULL 经 wire DTO 归 0，对齐原 SQL `rn=1 AND roe IS NOT NULL`）。
- 接线：`SetupRouter` 注入 `financialRepo` 的 `ds`；`NewFinancialRepository` 变参保其余调用点零改动。
- 测试：新增 `internal/repository/financial_ds_test.go`（httptest 假 datahub，`db=nil` ⇒ 走 ds 不触库）：
  `GetByCode`（`report_date DESC` 排序 + limit + 不下发 `last`）、`GetLatestByAnnounce`（`last=1` 取最新
  announce 一期 + 未命中 nil）、`GetPoolFinancial`（`last=1`/roe 空滤除）、「datahub 500 ⇒ 失败即抛」。
  **`go test ./...` 全绿**。
- 放行门（datahub 首灌 `financial_indicator` + 逐位对账零偏差）与停采 **待生产执行**。

## daily_price 读切换（qe + backend，2026-10-12）

第三个「qe + backend 同批切」的数据集，**读点最多、最高风险**（前复权因子/回测/指数条）。
**闸门默认关 = 逐字回退本地**。index 不是独立数据集：指数行情物理存在 `daily_price`
（code=`sh000300` 等），随本集一并切——qe 基准序列、backend 指数条都读它。

**datahub 侧**：注册 raw 数据集 `daily_price`（`db` provider 读自有库；列
`code,trade_date,open,high,low,close,volume,amount,adj_factor,turnover_rate`；
params `codes` 逗号 IN / `start`/`end` 区间 / `date` 精确（覆盖 start/end）/ `asof`(trade_date ≤) /
`last`(每码最近 N 行，服务端 `ROW_NUMBER OVER (PARTITION BY code ORDER BY trade_date DESC)`) /
`fields` 投影 / `adj_not_null`）。配套两个小数据集：`latest_trade_date`（params
`table`/`exclude_index`，列 `trade_date`）与（可选）`dup_keys`。见 datahub 仓 PR。

**qe 侧**（`quant-engine`）：
- `app/data_source.py` 增：`latest_trade_date(db)`（走 `latest_trade_date{exclude_index:true}`，
  闸门关 = 本地 `max(trade_date) where code NOT LIKE 'sh%'`）、`price_rows(db, codes, start, end, fields)`
  （区间 + `fields` 投影为**有序元组**）、`price_day(db, codes, d, fields)`（精确日；`codes` 空 = 全市场）、
  `price_latest(db, code, fields)`（`last=1`）、`market_ready(db, td)`（走 `market_ready{date}`）。
- 切读点：`factor_service.load_factor_inputs`（行情段）、`factor_trial.load_range_inputs`（行情段）、
  `factor_research.precompute_factor_stat`（前向收益行情段）、`performance.{_adj_series,_bench_series,`\
  `compute_attribution}`、`backtest/replay.preload`、`tasks.{latest_trade_date,market_ready}`、
  `morning_brief._latest_trade_date`、`notify_scheduler._schedule_matches`、`cli._default_trade_date`、
  `data_quality.{_market_latest,_check_coverage(with_bar),_check_benchmark,_check_duplicates(daily_price)}`。
  各移除不再使用的 `DailyPrice` import；**`data_quality.py` 保留 `DailyPrice` import**（其
  `_check_missing_days`、`_check_price_anomalies`、`_check_coverage` 的「上一交易日」推导仍走本地）。
- **有意保留本地**（datahub 无对应原语，标注 TODO）：`_check_missing_days`、`_check_price_anomalies`、
  `_check_coverage` 的 `prev` 推导（均需「上一开市日 / `max(trade_date < X)`」，datahub 无此谓词）；
  `backtest/engine.{_get_price,_get_fill_price,_get_prev_close,_get_benchmark_nav}`（Tier3 逐 (code,date)
  循环，本轮不接，Phase 4 停本地表前重构）。
- 测试：`tests/test_data_source.py` 增行情用例（rows 投影 / 空码短路 / `price_day` 有码与全码 /
  `price_latest` / `latest_trade_date` 本地 vs `exclude_index=true` / `market_ready` 覆盖 / `dup_keys` /
  失败即抛 / 回退）；**全量 `pytest` 227 passed**。

**backend（Go）侧**：
- `internal/datasource` 增 accessor：`DailyLatest(ctx, codes, n, asof)`（`daily_price` params
  `codes`/`last`/`asof`；服务端每码最近 n 行，返回按 `(code,trade_date ASC)` 升序）、
  `LatestFactor(ctx, code)`（`adj_not_null=true`+`last=1`）、`LatestTradeDate(ctx, excludeIndex)`（走
  `latest_trade_date`，Go 侧传 `exclude_index=false` 不过滤指数行——对齐本地 `ORDER BY trade_date DESC`）。
- Tier1 直接切：`DailyRepository.{GetRange→DailyRange, GetLatest→DailyLatest(n=1),`
  `GetLatestTradeDate→LatestTradeDate(false), GetLatestFactor→LatestFactor}`。
- Tier2「`last` 有界窗 + Go 归约」：`GetIndexQuotes`→`DailyLatest(n=2)`（指数条末两行）；
  `GetPoolMarket`→`DailyLatest(n=2)`（末两行现算 chg，对齐原 `LAG(close)`）；
  `GetSignalChg20`→`DailyLatest(n=21, asof=date)`（需满 21 行，`qfq=close×COALESCE(adj,1)`，
  `adjOrOne` 复现 COALESCE）。均**一次往返**（无 N+1）。
- 接线：`NewDailyRepository(db, ds...)`/`NewStockRepository(db, ds...)`/`NewSignalRepository(db, ds...)`
  变参注入；**tx-scoped 构造传 nil**（保事务内一致）；`GetByDate`/`GetPrevClose`（Tier3 tx 内逐码循环）保持本地。
- 测试：新增 `internal/repository/daily_ds_test.go`（httptest 假 datahub，`db=nil` ⇒ 走 ds 不触库，
  覆盖 `date`/`start-end`/`asof`/`adj_not_null`/`last` 窗与升序输出）：`GetRange`、`GetLatest`（末行即最新 +
  缺码 nil）、`GetLatestFactor`（空因子滤除）、`GetLatestTradeDate`（`exclude_index=false`）、
  `GetIndexQuotes`（`last=2`）、`GetPoolMarket`（chg 末两行）、`GetSignalChg20`（21 行→2000.0；20 行→无）、
  「datahub 500 ⇒ 失败即抛」。**`go test ./...` 全绿**。
- 放行门（datahub 首灌 `daily_price`（含指数行）+ 逐位对账零偏差）与停采 **待生产执行**。

## Phase 4 · Tier3 读点迁移（2026-10-13 起）

Tier3 读点在 Phase 3 **有意留本地**；`daily_price` 停采后本地副本**冻结在 2026-10-12**，
Tier3 读点读到陈旧数据。本阶段逐增量迁到 datahub，使本地 `daily_price` 可完全退役。
范围：引擎 **A** + 后端交易 **C** + `data_quality` **B**（含两处 datahub 新原语）；分 PR 串行；
闸门默认关 = 零行为变更、fail-closed。

### 增量 A — 引擎（`backtest/engine.py`，2026-10-13）

- **datahub 侧**：无新增（复用 `daily_price`）。
- **qe 侧**：
  - `_get_benchmark_nav` → `data_source.price_rows(db, ["sh000300"], 区间起, 区间止, ("trade_date","close"))`
    （原读本地**全表**；现按回测区间收窄并走 datahub，`Decimal→float` 已归一 ⇒ 字典逐位一致）。
    **这是引擎中唯一恒活（无策略短路）的本地读**：跨冻结日回测曾致 benchmark/excess_return 静默截断。
  - `_get_price` / `_get_fill_price`(t1_open) / `_get_prev_close` → `price_day` / `price_rows`（闸门关=逐字本地）。
    生产回测一律经 `ReplayStrategy`（实现 `price_at`/`open_at`/`prev_close_at`），此三方法兜底为**死路径**；
    迁移使「引擎读本地冻结 `daily_price`」彻底清零。
  - 移除 `DailyPrice` / `select` import。
- **测试**：`tests/test_backtest.py` 增 `_get_benchmark_nav` 闸门开单测（boom-db + fake client，断言
  一次往返、区间缓存、**不触本地库**）；既有回测套件（闸门关）全绿；`pytest` 228 passed。

### 增量 C — 后端交易批量快照（`daily_repo.go` + `service/trading.go`，2026-10-13）★ 修停摆

- **datahub 侧**：无新增（复用 `daily_price` 的 `last` 有界窗）。
- **backend 侧**：
  - `DailyRepository` 增**当日快照** `PrefetchDay(ctx, codes, tradeDate)`：闸门开 →
    `DailyLatest(codes, last=2, asof=tradeDate)` **恰一次**往返，按升序 `(code, trade_date)` 归约——
    末行 `trade_date==当日` ⇒**当日 bar**（供 `GetByDate`）+ 前一行收盘（供 `GetPrevClose`）；
    末行 `< 当日`（当日停牌/无数据）⇒ 当日无 bar、prev close = 末行；该码全无行 ⇒ 当日 `nil` /
    prev `(0,false)`。闸门关（ds 为 nil / 未开 / codes 空）⇒ no-op。
  - `GetByDate` / `GetPrevClose` **首检快照**（`dayLoaded && 同日` → 走内存映射，**不触 gorm**）；
    否则**逐字回退**原本地实现（零行为变更；tx-scoped 构造不传 ds）。
  - `ExecuteDay`：`dr := NewDailyRepository(tx, s.ds)`；在逐码循环**之前**聚集 code 集合
    （`ledger.positions` ∪ `or.GetPending` ∪ 当日 SELL/BUY signals，须**上移** `sr.GetSignals`
    两 action 的纯读）→ `dr.PrefetchDay(...)` 一次预取；失败即 `return err`（回滚 = fail-closed）。
    下游逐码 `GetByDate`/`GetPrevClose` 调用点**零改动**。
  - **修停摆**：冻结日后 `GetByDate(code, today)→nil` ⇒ mark-to-market 保留旧价、`fillStrategy`
    全「停牌跳过」、`execManual` 全「当日无行情」拒单 ⇒ **自 10-13 起实际不再成交**；快照后恢复。
- **等价/风险**：`ExecuteDay` 事务**不写 `daily_price`** ⇒ 快照一致（且比「读冻结本地」更正确）；
  全程**恰一次**往返（N 码不变，避免事务内 N+1）。风险＝**快照完整性**（漏码 ⇒ 误判停牌 ⇒ 静默跳单）
  ⇒ 预取**必在全部 code 集已知后**执行，并加完整性测试（覆盖 SELL+BUY）。
- **测试**：`internal/repository/daily_ds_test.go` 增 `PrefetchDay`（命中快照 + **恰一次**往返 +
  `last=2&asof` 断言）、当日停牌码、当日上市码（无前收）、缺码、fail-closed（500 即抛）、闸门关 no-op；
  `internal/service/trading_ds_test.go` 增 `ExecuteDay` 端到端（本地 `daily_price` **不种**=模拟冻结，
  datahub 有当日行情 ⇒ `BuyCount==1`、恰一次 `last=2&asof`、mark-to-market 取新价）。`go test ./...` 全绿。

## 遗留

- backend（Go）读切换 `stock_basic`、`daily_valuation`、`financial_indicator`、`daily_price`（含 index）**已全部落地**；四集 **2026-10-13 生产翻读 + 停 steady 采集**（datahub 唯一采集方，见 runbook §5）。
- **Tier3 读点迁移**（Phase 4，进行中）：引擎（`backtest/engine`）**已迁**（增量 A，见上）；backend
  `GetByDate`/`GetPrevClose`（交易批量快照）**已迁**（增量 C，见上）；`data_quality` prev 推导（增量 B）待迁。
- collector 读切换（随 collector 退役）。
- 过渡期告警断链（datahub 写自有库 `task_run` → steady `notify_scheduler` 不读，暂看 datahub-collector `/healthz`）仍待解。
