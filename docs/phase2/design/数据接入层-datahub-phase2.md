# 数据接入层 datahub — Phase 2 实施蓝图

> **状态**：📋 设计（2026-10-08）｜**上位文档**：[`数据接入层-datahub.md`](数据接入层-datahub.md)（架构总设计）、[`数据接入层-datahub-phase1.md`](数据接入层-datahub-phase1.md)（Phase 1 蓝图）
> **一句话**：datahub **建自有库**并**接管核心原始采集**（行情/估值/财务/日历/股票列表），**逐数据集对账后切换为唯一采集方**；对外**首次暴露研究数据**。**steady 读取不改**（Phase 3 才改），本地 collector 保留但停跑对应任务（作灰度回退）。

---

## 0. 目标 / 非目标

**目标**
- datahub 建**自有库**（原始表 schema）。
- 把核心采集器从 steady collector **move** 进来（含校验守卫）。
- **逐数据集灰度**：采集 → 对账 → 切唯一采集方 → 观察一个交易日周期。
- 对外（HTTP/MCP）**首次提供研究数据**（行情/估值/财务/日历/股票列表）——兑现其他项目的原始诉求。

**非目标（推迟 Phase 3）**
- **不改 steady 读取**：quant-engine/backend 仍读 steady 本地原表。
- **不删** steady collector 代码（保留作回退；Phase 3 收尾才删）。
- **不 drop** steady 原表。

---

## 1. 自有库 schema（datahub DB）

- **原始表**（迁自 steady，列/类型/索引/唯一键**逐字对齐**）：`stock_basic`、`daily_price`、`daily_valuation`、`financial_indicator`、`trade_calendar`、`market_hotspot`。
- **编排/台账**：`task_run`（采集台账，同构 steady）、`schema_migrations`（迁移台账）、采集就绪相关表。
- **DDL 来源**：从 `deploy/postgres/init.sql` + `deploy/migrations/`（如 `007_data_scope_and_turnover.sql`）**抽取原始表部分**（非全搬；计算表 `factor_*`/`strategy_*`/`account*` 等留在 steady）。
- **迁移机制**：datahub 自带（照 `scripts/migrate.sh` 范式：按序应用 + `schema_migrations` 台账 + 幂等）。
- **必须对齐的关键约束**：`uq_daily_price_code_date (code, trade_date)`、`daily_price.trade_date` 索引、`factor_value` 相关唯一键、`data_scope`（采集域）语义——**对账可比 + Phase 3 steady 读到等价数据**都依赖它。
- **待验证**：datahub 侧与 steady 侧 schema「列漂移」检测（对齐后加契约测试）。

---

## 2. 采集器迁移（move，非 copy）

从 `collector/` **move**（含踩坑补丁，一并搬）：
- `app/sources/`：`baostock.py`、`tencent.py`、`net.py`
- `app/collectors/`：`daily.py`、`valuation.py`、`finance.py`、`index.py`、`calendar.py`、`stock.py`、`hotspot.py` + `base.py` + `scope.py`
- 配置门控：`config.py` 的 `baostock_enabled` / `tencent_enabled` / `daily_source_chain` / `collect_scope`
- **校验/守卫（随采集搬）**：`factor_guard` / `cross_check_splits` / `clean_daily_rows` / `upsert`（全 None 剔除）
- 调度：APScheduler（照 `collector/app/tasks.py`）→ datahub 采集调度；`watchdog.py` 守护一并搬

**闸门**：移过来的采集任务 `DATAHUB_COLLECT_ENABLED` × `DATAHUB_COLLECT_DATASETS`（逗号白名单），**默认空 = 不采集**（部署零行为变更）。

---

## 3. Provider 增类：`raw`（读自有库）

- Phase 1 只有 `external` provider（抓 + 缓存）。
- Phase 2 增 `db` provider（读**自有库**）→ 数据集 `kind="raw"`。
- 契约扩展为总会设计 §2.2 的完整清单：`stock_basic` / `daily_price` / `daily_valuation` / `financial_indicator` / `trade_calendar` / `latest_trade_date` / `market_ready` / `market_hotspot` / `index_quotes`，**批量形态**（对齐 `load_factor_inputs`/`preload`，防 N+1）。
- 对外 exit 与 MCP tool 由契约机械生成（同 Phase 1 机制）。

---

## 4. 逐数据集灰度切换（本阶段核心）

**切换顺序（低 → 高风险）**：
```
hotspot(Phase1 已有) → calendar → stock_basic → index → valuation → finance → daily(最高风险，放最后)
```

**每个数据集四步**（任一步失败即停、回退）：

| 步 | 动作 | 判定 |
|---|---|---|
| 1 | datahub 采集并落库 | 有数据、任务绿 |
| 2 | **对账**（见 §5） | 逐位零偏差 |
| 3 | 切**唯一采集方**（停 steady 该采集器） | steady 该任务停跑、datahub 接管 |
| 4 | 观察**一个完整交易日周期** | 采集新鲜度/覆盖/因子不变量（steady 侧 `factor_value` 恒 800） |

**闸门**：datahub 侧 `DATAHUB_COLLECT_DATASETS` 白名单；steady 侧新增**停采闸门**（env 控制 collector 跳过指定任务）。
**双份防护（硬约束）**：同一数据集在**同一时刻只能有一个采集方**。

**本阶段进度（2026-10-09）**：
- **calendar 已切**（步 1–3 完成）：datahub 采集落库 → 与 steady 逐位对账**零偏差**
  （锚定 `--end` 窗口 60/60 `accepted`；脚本 `datahub/scripts/reconcile_calendar.py`）。
  步 3（停 steady 该采集器）**已生效**：steady 发布 `steady-20261009-1a43274`（PR #34）落地
  停采闸门代码 + 生产 `.env` 加 `COLLECTOR_DISABLED_JOBS=job_sync_calendar` 重启 collector
  （日志「停采闸门：跳过注册 job_sync_calendar」，补跑探针亦跳过）。**datahub 为 calendar 唯一采集方**。
  步 4（观察一个完整交易日周期）进行中。
- 其余数据集 `stock_basic → index → valuation → finance → daily` 待续（`daily` 最高风险放最后）。

---

## 5. 对账机制（避免"并行双采"踩上游限流）

**问题**：若切换期 datahub 与 steady **并行双采同一源** → 争抢上游限流（BaoStock 10001011、东财 HTTP 000 的历史痛点）。

**解法：回填对账（非并行 live）**
1. datahub 采一段**历史窗口**（如近 60 交易日）→ 落 datahub 库；
2. 与 steady **已存同窗口**逐位比对——steady 那段是「旧实现产物」，作**基准**；
3. 因是**历史拉取**（源支持历史：腾讯日K / BaoStock 日线均给历史），不占用当日采集窗口，**不与 steady 当日采集争抢**；
4. 沿用仓库 `classify` 五类（`accepted`/`db_anomaly`/`false_pos`/`drifted`/`rejected`）判读偏差。
5. **live 切换后**的观察期，用「ts / 行数 / 覆盖 / 就绪度」健康指标验证（不再逐位，因无第二方）。

> 可选兜底：短**并行窗**（严格错峰 cron）仅在回填对账不可行（源无历史）时使用，不首选。

---

## 6. 编排 / 台账 / 健康

- datahub 采集台账 `task_run`（同构 steady：`task_name+run_date` 幂等、detail 结构化）。
- 就绪闸门 `market_ready`（覆盖率达阈值才算"可算因子"）；`latest_trade_date`。
- `/healthz` 扩展：DB 可达 + 采集新鲜度。
- 守护：搬 `watchdog.py`（job 级超时/退出计数），防采集卡死（Issue #14 教训）。

---

## 7. steady 侧本阶段改动（最小）

- **只加"停采闸门"**：给 collector 加 env 控制的「跳过某采集任务」，供 §4 步 3 使用。
  已落地形态（黑名单，默认空 ⇒ 行为零变化）：
  ```
  COLLECTOR_DISABLED_JOBS=              # 逗号黑名单，值=job 函数名（如 job_sync_calendar）
  ```
  命中即：①**不注册**到 scheduler（`tasks._add_job`）；②**不注册补跑探针**
  （`register_catchups`）——防 `watchdog.startup_catchup` 把已停 job 复活。
  选黑名单而非白名单：白名单默认会停掉未列出的全部 job（落码即改生产行为），
  黑名单是唯一能保证「落码即零变更」的形态。
- **不改读取**（Phase 3）。
- collector 代码**保留**（灰度回退用）；`import akshare` 等直调仍在，但对应任务已停跑。

---

## 8. 部署

- datahub 复用**生产 PG 实例另建独立库 `datahub`**（决策 **D1**，2026-10-09；非自带 postgres）。
  datahub 双服务**加入 PG 所在 docker 网络**按容器名 `quant-postgres` 连
  （不用 `host.docker.internal`——生产 PG 仅监听 `127.0.0.1:5432`，经 host-gateway 连被拒）；
  连接池调小（与 steady 共享 `max_connections`）。
- 初始 schema 迁移（§1）：`scripts/init-db.sh` 建库 + `scripts/migrate.sh` 迁移台账。
- **备份**：原始数据是命根子 → 备份策略（沿用仓库 02:30 备份范式，异地更佳）。

---

## 9. 测试 / 验收

- [ ] 每个数据集**回填对账零偏差**（或偏差已 classify 归类并知情接受）。
- [ ] **采集唯一出口**：切换后 steady 无活跃外部采集（grep + 台账双重）。
- [ ] **闸门回退**验证：关 datahub 采集 → steady 恢复可采（一键回退）。
- [ ] **一个完整交易日周期稳定**：采集新鲜度/覆盖绿；steady 侧 `factor_value` 当日恒 800、信号正常。
- [ ] schema 对齐测试（datahub vs steady 原始表列一致）。

---

## 10. 风险与取舍

| 风险 | 说明 | 缓解 |
|---|---|---|
| **daily 最高风险** | 涉 `guard_factor` / 全史 adj_factor 重写 / 复权口径 | **放最后**；单独立契约 + fixture 测试；对账窗口拉长 |
| **datahub 成采集 SPOF** | 独立部署，挂了采集停 | 备份 + 守护 + **一键回退闸门**（steady 恢复采集） |
| **对账口径** | 后复权/单位（×100/×10000 历史坑） | 冻结口径，契约显式单位 |
| **双份取数** | 并行切换易双采 | §5 回填对账（默认不并行） |
| **schema 漂移** | 两库列不一致 | 契约 + 对齐测试 + 迁移台账 |

---

## 11. 未决

1. ~~**datahub DB 选型**：自带 postgres vs 复用现有实例（独立部署倾向自带）。~~
   **已定（2026-10-09，D1）**：**复用生产 PG 实例另建独立库 `datahub`**（不自带 postgres）；
   两服务加入 PG docker 网络按容器名连（见 §8）。
2. ~~**采集台账 schema**：复用 steady `task_run` 结构 vs 另立。~~
   **已定（2026-10-09）**：**复用 steady `task_run` 结构**（列/类型逐字对齐），落 datahub 库。
3. ~~**对账窗口长度**：历史回填深度（60 交易日？）与源可回溯上限。~~
   **已定（2026-10-09）**：**日历取全量**（源一次给长约 2 年+未来 1 年，无需分批）；
   **其余数据集 60 交易日**逐位比对（锚定同一 `--end` 上界，防两侧窗口错位）。
4. ~~**停采闸门形态**：steady 侧 env 白名单/黑名单还是 per-task 开关。~~
   **已定（2026-10-09）**：黑名单 `COLLECTOR_DISABLED_JOBS`（逗号，值=job 函数名），
   默认空 ⇒ 零行为变更；命中则不注册任务、不接补跑探针（见 §7）。
