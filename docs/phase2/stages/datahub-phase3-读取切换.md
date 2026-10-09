# datahub Phase 3 · 读取切换（calendar 增量）

> 数据接入层 datahub 第三阶段：**steady 原始数据读取从本地库切到 datahub API**（按需 HTTP，单一真源）。
> 总设计 [`../design/数据接入层-datahub.md`](../design/数据接入层-datahub.md) §5；本阶段蓝图
> [`../design/数据接入层-datahub-phase3.md`](../design/数据接入层-datahub-phase3.md)。
> 承接 [`datahub-phase2-地基搬栈.md`](datahub-phase2-地基搬栈.md)（采集已切 datahub）。

**状态**：✅ calendar 读切换**已生产落地**（2026-10-09，发布 `steady-20261009-87e7dc7`）。

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

## 遗留

- backend（Go）读切换、collector 读切换（随 collector 退役）。
- 过渡期告警断链（datahub 写自有库 `task_run` → steady `notify_scheduler` 不读，暂看 datahub-collector `/healthz`）仍待解。
