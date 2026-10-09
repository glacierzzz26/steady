# datahub Phase 3 · 读取切换（calendar 增量）

> 数据接入层 datahub 第三阶段：**steady 原始数据读取从本地库切到 datahub API**（按需 HTTP，单一真源）。
> 总设计 [`../design/数据接入层-datahub.md`](../design/数据接入层-datahub.md) §5；本阶段蓝图
> [`../design/数据接入层-datahub-phase3.md`](../design/数据接入层-datahub-phase3.md)。
> 承接 [`datahub-phase2-地基搬栈.md`](datahub-phase2-地基搬栈.md)（采集已切 datahub）。

**状态**：🚧 进行中（calendar 增量已生产落地；**backend Go 侧读取切换已纳入范围**，剩余数据集 `stock_basic→index→valuation→finance→daily` 逐集全迁移）。

## 剩余数据集逐集全迁移（2026-10-09 起）

**范围**：把 `stock_basic / daily_price / daily_valuation / financial_indicator / index` 逐个从「steady 采 + steady 读」迁到「datahub 采 + steady 经 HTTP 读」。
**关键约束**：每个数据集的读消费方有两处（quant-engine + **backend Go**）；**停 steady 采集**是该数据集最后一步，须二者都已切读（否则 backend 服务陈旧数据）。
**每数据集范式**：① datahub 注册 raw 数据集 + 采集放闸 + 首灌 + **对账零偏差** → ② quant-engine `data_source.py` 增函数 + 改读点 + 翻闸 → ③ backend `internal/datasource` 增 accessor + 改 repo + 翻闸 → ④ 停 steady 采集（`COLLECTOR_DISABLED_JOBS`） → ⑤ 文档/归档。顺序 `stock_basic → index → valuation → finance → daily`（daily 最后，风险最高）。

**Increment 0（backend 基建，✅ 代码落地 2026-10-09，零行为变更）**：
- `internal/config`：`DatahubConfig` + `ReadEnabled`（`DATAHUB_*` env，与 quant-engine 同源）+ `getEnvList/getEnvBool/ParseDur`。
- `internal/datahub`（唯一出网点）：`FetchRaw`（Bearer/信封/瞬时重试/TTL 缓存/`Reset`）+ `Date` 适配器 + typed errors + 包级泛型 `Fetch[T]`。
- `internal/datasource`（唯一切换点）：`Source`（`Enabled/FallbackLocal/Reset`）+ stock_basic/calendar/daily/valuation/financial accessor 与 wire DTO。
- 测试：`client_test.go`（httptest）+ `config_test.go` + `source_test.go`；`go test ./...` 全绿。
- **默认 `read_datasets: []` + token 空 ⇒ 恒 disabled ⇒ 部署零行为变更**。repo 方法切换 + 接线随各数据集增量推进（Tier1/2/3 分级见设计 §6）。


## 目标

把 quant-engine 的原始数据读取从「裸查本地表」改为集中入口 `app/data_source.py`，按 env 闸门走
datahub HTTP。**首个增量 = calendar**（`trade_calendar` 已由 datahub 采），为后续数据集验证整套读切换机制。
闸门默认关 → 部署零行为变更；本地库不删、collector 不删（作回退）。

## 时间

| 起 | 止 | 说明 |
|---|---|---|
| 2026-10-09 | — | 代码/测试落地；datahub 全史补录 + 部署翻闸待办 |

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

## 待办

3. **生产部署**：steady 发布 → `.env` 加 `DATAHUB_*`（白名单先留空）→ 验零行为 → 置
   `DATAHUB_READ_DATASETS=trade_calendar` → `--force-recreate quant-engine` → 验读 datahub。
4. 观察一个完整交易日周期。

## 验证（已做）

- ✅ `pytest tests/` **181 passed**（含新增 20）。
- ✅ **放行门**：datahub 全史补录 + 全区间对账零偏差（见上）。
- ⬜ 生产端到端（待部署）。

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

## 遗留

- 后续数据集 `stock_basic → index → valuation → finance → daily` 逐个增补到 `data_source.py` 并翻闸。
- backend（Go）读切换、collector 读切换（随 collector 退役）。
- 过渡期告警断链（datahub 写自有库 `task_run` → steady `notify_scheduler` 不读，暂看 datahub-collector `/healthz`）仍待解。
