# datahub Phase 3 · 读取切换（calendar 增量）

> 数据接入层 datahub 第三阶段：**steady 原始数据读取从本地库切到 datahub API**（按需 HTTP，单一真源）。
> 总设计 [`../design/数据接入层-datahub.md`](../design/数据接入层-datahub.md) §5；本阶段蓝图
> [`../design/数据接入层-datahub-phase3.md`](../design/数据接入层-datahub-phase3.md)。
> 承接 [`datahub-phase2-地基搬栈.md`](datahub-phase2-地基搬栈.md)（采集已切 datahub）。

**状态**：🚧 进行中（代码 + 测试已完成；**datahub 全史补录 + 生产部署翻闸待办**）。

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

## 待办（放行门）

1. **datahub 全史补录**：走 AkShare 全史源补 `datahub.trade_calendar` 至 ⊇ steady（1990→≥2027）。
2. **全区间对账**：`reconcile_calendar.py` 放大到全量，逐位零偏差。
3. **生产部署**：steady 发布 → `.env` 加 `DATAHUB_*`（白名单先留空）→ 验零行为 → 置
   `DATAHUB_READ_DATASETS=trade_calendar` → `--force-recreate quant-engine` → 验读 datahub。
4. 观察一个完整交易日周期。

## 验证（已做）

- ✅ `pytest tests/` **181 passed**（含新增 20）。
- ⬜ 生产端到端（待部署）。

## 遗留

- 后续数据集 `stock_basic → index → valuation → finance → daily` 逐个增补到 `data_source.py` 并翻闸。
- backend（Go）读切换、collector 读切换（随 collector 退役）。
- 过渡期告警断链（datahub 写自有库 `task_run` → steady `notify_scheduler` 不读，暂看 datahub-collector `/healthz`）仍待解。
