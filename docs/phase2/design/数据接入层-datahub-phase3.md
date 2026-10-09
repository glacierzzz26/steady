# 数据接入层 datahub — Phase 3 实施蓝图（读切换）

> **状态**：📋 设计（2026-10-09）｜**上位文档**：[`数据接入层-datahub.md`](数据接入层-datahub.md)（总设计 §5 消费面改造）、[`数据接入层-datahub-phase2.md`](数据接入层-datahub-phase2.md)（采集接管蓝图）
> **一句话**：datahub 已接管采集 → 本阶段把 steady 的**原始数据读取**从本地库切到 datahub API（**按需 HTTP**），实现**单一真源**；数据集中**逐项灰度**、env 闸门控制（默认读本地=零行为变更）。

---

## 0. 目标 / 非目标

**目标**
- quant-engine 原始数据读取从「裸查本地表」改为集中入口 `app/data_source.py`，按闸门走 datahub HTTP。
- **逐数据集灰度**：先 calendar，再 stock_basic/index/valuation/finance/daily。
- 闸门 `DATAHUB_READ_DATASETS`（逗号白名单）默认空 → 部署**零行为变更**，一键回退。

**非目标（延后）**
- backend（Go）读切换、collector 读切换（随 collector 退役）。
- 删本地原始表、删 collector 代码。
- calendar 之外的其它数据集（各自接管次序到位后再逐个切）。

---

## 1. 读闸门与客户端（零行为变更形态）

`app/config.py`（函数形式，调用时读 env）：

```
DATAHUB_READ_DATASETS=          # 逗号白名单，值 = dataset id（如 trade_calendar）；空 = 全读本地
DATAHUB_BASE_URL=http://datahub:8100
DATAHUB_TOKEN=                  # 须 = datahub 服务侧值（否则 401，fail-closed）
DATAHUB_HTTP_CONNECT_TIMEOUT=5  DATAHUB_HTTP_READ_TIMEOUT=10
DATAHUB_RETRIES=1               DATAHUB_RETRY_DELAY=0.5
DATAHUB_CACHE_TTL=60            # 进程内只读缓存（§5.3 性能优化，非权威副本）
DATAHUB_FALLBACK_LOCAL=0        # 故障回退本地（默认 0=失败即抛）
```

判定 = `base_url 非空 × token 非空 × dataset ∈ DATAHUB_READ_DATASETS`。

`app/datahub_client.py`（**唯一出网点**）：`DatahubClient.fetch(id, params) -> list[dict]`。
- Bearer 鉴权；信封 `{code,message,data,meta}` → `data`；`code!=0`/非 JSON → `DatahubParseError`。
- 重试**只对瞬时故障**（超时/连接错/5xx）；4xx 快速失败（含 401）。
- 显式 `timeout=(connect,read)`；**不安装 `requests.Session` 全局补丁**（遵 collector `sources/net.py`
  「补丁仅 `__main__` 安装」纪律，避免污染测试进程网络调用）。
- 进程级单例 + 短 TTL 缓存；`reset_client()` 供测试。

`app/data_source.py`（**唯一切换点**）：闸门关 → 本地 SQL（逐字一致）；闸门开 → datahub。

---

## 2. 首个增量：calendar 读切换（R1–R6）

`trade_calendar` 已由 datahub 采（Phase 2），steady 读取仍本地。本增量切其六个读点：

| # | 读点 | 替换 |
|---|---|---|
| R1 | `morning_brief._is_open` | `data_source.is_open(d, db)` |
| R2 | `notify_scheduler._schedule_matches`（morning_brief 分支） | `data_source.is_open(td, db)` |
| R3 | `watchdog.startup_catchup` | `data_source.is_open(date.today(), db)` |
| R4 | `backtest/engine._get_trading_dates` | `[d.isoformat() for d in data_source.cal_dates(start, end, db)]` |
| R5 | `backtest/replay.preload`（grid） | `data_source.cal_dates(start, end, db)` |
| R6 | `data_quality._check_missing_days` | `data_source.recent_open_days(latest, MISSING_DAY_WINDOW, db)` |

`data_source` 三函数语义（保返回类型）：`cal_dates` 升序 `list[date]`；`is_open` 无记录=False；
`recent_open_days` `≤end` 最近 N 个**降序** `list[date]`（datahub 无服务端 limit → 放宽窗口翻倍拉取）。

### 前置：datahub 日历覆盖必须 ⊇ steady（✅ 已过，2026-10-09 生产）

实测两侧覆盖曾悬殊：steady `trade_calendar` 8958 行（1990-12-19 → 2027-08-26），datahub 仅
545 行（2024-10-09 → 2026-12-31，受 BaoStock `trade_cal_rows` 默认 ±2 年窗口所限）。**若直接切，
回测（R4/R5 任意区间）会静默截断网格 → 结果错**。故切读前须：
1. **补齐全史**：走 datahub AkShare 路径 `ak.tool_trade_date_hist_sina()`（新浪源返回全部交易日）
   一次性 upsert 进 `datahub.trade_calendar`（幂等，PK=`cal_date`）。
2. **全区间对账**：`datahub/scripts/reconcile_calendar.py --all`，1990→2027 逐位比对，**零偏差**为放行门。

**✅ 结果（2026-10-09）**：datahub 补至 **8797 行**（1990-12-19 → 2026-12-31）；`--all` 对账
`datahub=8738 steady=8738 锚点=2026-10-09`，**accepted 8738 / 其余 0，退出码 0**。

---

## 3. 失败模式（决策）

**失败即抛（不静默回退本地）**：datahub 权威、本地是待退役副本；静默回退会掩盖故障。调用方已有
`try/except` 降级（morning_brief 记 failed、notify tick 每事件捕获、startup_catchup 捕获）。
应急开 `DATAHUB_FALLBACK_LOCAL=1` 仅作首窗口兜底，**默认关**。

---

## 4. 验证

- **前置**：datahub 全史补录 → count/min/max ⊇ steady；全区间对账零偏差。
- **零行为**：部署后白名单空 → `job_morning_brief`/`job_data_quality`/一次回测结果与改前一致。
- **切换**：置 `DATAHUB_READ_DATASETS=trade_calendar` 重启 → 结果一致；datahub 访问日志约 1 次/分（TTL 60s）。
- **容错**：停 datahub → 读路径失败即抛（错误清晰、不静默给旧值）；恢复后正常。
- **回退**：清空白名单重启 → 回本地读（一键）。

---

## 5. 风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| **日历覆盖** | datahub 2 年窗 vs steady 全史 → 回测截断 | §2 前置补全史 + 全区间对账（未过不放行） |
| **空结果不可分** | `[]` 与真实休市同形 | 全史补齐后交易日必有行；监控日历新鲜度 |
| **token/可达性** | 401 或网络不通 | 翻闸前 curl 核对；**每次发布复验** datahub 在 steady 网络内 |
| **datahub SPOF** | 读路径新增一跳 | datahub 自备 restart/healthcheck；缓存降抖动；一键回退 |
| **新依赖 requests** | quant-engine 首次引入 | 与 collector 一致；镜像小幅增大 |
