# datahub Phase 2 · 地基 + 搬栈 + calendar 切换

> 数据接入层 datahub 第二阶段：**建自有库 + 搬核心采集栈 + 首个数据集（calendar）灰度切换**。
> 总设计 [`../design/数据接入层-datahub.md`](../design/数据接入层-datahub.md)；
> 本阶段蓝图 [`../design/数据接入层-datahub-phase2.md`](../design/数据接入层-datahub-phase2.md)。
> datahub 实现真源在独立仓库 [glacierzzz26/datahub](https://github.com/glacierzzz26/datahub)（`dev`）。

## 目标

把「对外部源的采集」从 steady collector 物理搬到**独立仓库/独立部署**的 datahub，并让它
**建自有库、逐数据集接管核心原始采集**。本阶段只做**风险最低、可独立验证**的一段——
**地基 + 搬栈 + calendar 对账切换**，为后续数据集（stock_basic/index/valuation/finance/daily）
验证整套灰度机制。**steady 读取不改**（Phase 3 才改），本地 collector 保留作回退。

## 时间

| 起 | 止 | 说明 |
|---|---|---|
| 2026-10-09 | 2026-10-09 | datahub PR #4–#9 + steady PR #32；建库/部署/对账同日完成 |

## 设计

**关键决策**（详见 phase2 蓝图 §3/§7/§8/§11）：

- **D1 · DB 选型 = 复用生产 PG 实例另建独立库 `datahub`**（非自带 postgres）。两服务**加入 PG
  所在 docker 网络**按容器名 `quant-postgres` 连；**弃用 `host.docker.internal`**（生产 PG
  仅监听 `127.0.0.1:5432`，经 host-gateway 连被拒，实测 `Connection refused`）。网络名由
  steady 固定项目名 `steady-20260821-c8d0651` 决定，跨发布稳定。DB env 名逐字保留 `DB_*`。
- **单镜像双服务**：同一 `datahub:<ver>` 镜像起 `datahub`（API:8100）+ `datahub-collector`
  （采集:9200，`command: python -m app.tasks`）。理由：① `BlockingScheduler` 与 uvicorn 同进程
  会饿死 ASGI；② watchdog `os._exit(1)` 依赖 restart，同进程会连 API 一起打下去；③ 端口独立探活。
- **采集双闸门（默认全关）**：`DATAHUB_COLLECT_ENABLED × DATAHUB_COLLECT_DATASETS`；**两层**——
  注册层（`register_jobs` 只为已放闸数据集 `add_job`）+ 调用层（`@collect_gated(dataset)` 挡
  `watchdog.startup_catchup`/手工 `cli` 绕过）。全关 ⇒ 不注册任务、不启补跑、不发外部请求。
- **停采闸门 = 黑名单**（steady 侧，`COLLECTOR_DISABLED_JOBS`，默认空=零行为变更）。选黑名单而非
  白名单：白名单默认会停掉未列出的全部 job（落码即改生产行为），黑名单是唯一「落码即零变更」形态。
- **对账窗口必须锚定**：两侧日历**最大日期常不同**（steady 预载到 2027、datahub 只到采集窗末），
  各取「最近 N 行」会取到**不重叠的两段** → 逐位比对全成伪偏离（实测 60/60 假 `db_anomaly`）。改为
  共享 `--end` 锚点（默认今天），两侧按 `cal_date <= end` 取最近 N 行。
- **raw 语义**（vs external）：① **不受** `DATAHUB_EXT_*` 闸门约束（读自有库）；② **不缓存**；
  ③ **空结果是合法值**（= 尚未采集），**不**转 503。

## 实现

datahub（PR #4–#9，`dev`）：

- `3ae1dac` 建库地基——原始表 schema（`deploy/postgres/init.sql`）+ 迁移机制 + 跨仓库对齐测试
  （`tests/fixtures/steady_raw_schema.sql` vendor 冻结副本 + `test_schema_parity.py`）。PR #4。
- `caa0f81` 配置 + DB 连接 + ORM 模型（`collect_config.py`/`db.py`/`models/tables.py`）。PR #5。
- `b58d029` 搬入核心采集栈 + 采集双闸门（`sources/`·`collectors/`·`cleaners/`·`tasks.py`·
  `watchdog.py`·`cli.py`；`from app.config`→`collect_config`、`from app.sources.net`→`providers.net`）。PR #6。
- `9a11c80` 首个 raw 数据集 `trade_calendar` + `db` provider（`datasets/raw.py`·`providers/db.py`
  + `service.py` kind 分支）。PR #7。
- `2745af7` calendar 对账脚本 `reconcile_calendar.py`（切换放行门，五类词表）。PR #8。
- PR #9（`fix/phase2-reconcile-db-network`，**待合并**）：对账窗口 `--end` 锚定 + datahub↔PG 网络。

steady（PR #32，`dev`）：

- `fd45acd` 停采闸门 `COLLECTOR_DISABLED_JOBS`——`tasks.py` 用 `_add_job` 包裹全部 `add_job` +
  `register_catchups` 跳过；`config.job_disabled`；`tests/test_disabled_jobs_gate.py`。默认空=零变更。
- 发布 `steady-20261009-1a43274`（dev→master，PR #34）落地停采闸门代码；生产 `.env` 加
  `COLLECTOR_DISABLED_JOBS=job_sync_calendar` → `--force-recreate collector`，calendar 切唯一采集方。

运维（生产，2026-10-09）：

- 建库 `datahub`（`scripts/init-db.sh`）+ 施 schema（`scripts/migrate.sh --check` 无漂移）。
- 部署 `datahub:v0.0.0-f6a1d56` 双服务（本地构建 → LAN 传输；生产拉 GitHub 慢）。采集闸门先关。
- 翻闸 calendar：`DATAHUB_COLLECT_ENABLED=1` + `DATAHUB_COLLECT_DATASETS=calendar`
  + `BAOSTOCK_ENABLED=1` + `BAOSTOCK_SOURCES=calendar`；`--force-recreate`。
- 回填 datahub `trade_calendar`（BaoStock 一次给长约 2 年+未来 1 年）→ 对账。

## 验收

- ✅ **对账零偏差**：`reconcile_calendar.py --limit 60` → `datahub=60 steady=60 锚点=2026-10-09`，
  `accepted 60 / drifted 0 / db_anomaly 0 / false_pos 0 / rejected 0`。
- ✅ **地基对齐**：`test_schema_parity.py` 断言 datahub `init.sql` vs steady 原始表冻结副本一致。
- ✅ **部署零行为**：采集闸门先关时 `scheduler.get_jobs()==[]`、不发外部请求；API `/v1/healthz` 200。
- ✅ **raw 出口**：`trade_calendar` 经 HTTP `/v1/datasets/trade_calendar` 与 MCP tool 双出口可取。
- ✅ **工程门禁**：datahub `ruff` 干净 / `pytest` **322 passed, 2 skipped**；steady 停采用例绿。
- ⚠️ **步 4（观察一完整交易日周期）进行中**：切唯一采集方已生效（见下），仍需观察采集新鲜度/
  覆盖与 steady 侧 `factor_value` 恒 800。

## 遗留

- ~~**切唯一采集方**~~ **已完成（2026-10-09）**：steady 发布 `steady-20261009-1a43274`（PR #34，
  dev→master）落地停采闸门代码 → 生产 `.env` 加 `COLLECTOR_DISABLED_JOBS=job_sync_calendar`
  → `--force-recreate collector`。验证：collector 日志「停采闸门：跳过注册 job_sync_calendar」
  （补跑探针亦跳过）；`scheduler` 注册列表无该 job（其余 8 个 job 正常）。
  **回退顺序（敏感）**：① 先删 steady 的 `COLLECTOR_DISABLED_JOBS` 重启（恢复采集）→ ② 再删
  datahub 的 `calendar` 白名单重启（先恢复 steady 保证无覆盖空窗）。回滚锚点：生产
  `~/steady-20261009-1a43274/.env.pre-stopgate-20261009`。
- **PR #9 已合并**（datahub）：对账 `--end` 锚定 + PG 网络修复（含 README/COMPOSE 文档同步）。
- **后续数据集**：`stock_basic → index → valuation → finance → daily`（`daily` 最高风险放最后，
  涉 `guard_factor`/全史 adj_factor 重写，须逐位对账）。
- **⚠ 告警断链（已知缺口）**：搬来的 watchdog 把 failed 写 **datahub** 库 `task_run`，而
  quant-engine `notify_scheduler` 读 **steady** 库 → 过渡期采集失败不再推飞书；信号暂看
  datahub-collector `/healthz`（watchdog status_provider）。datahub 侧告警器为后续项。
- **schema 漂移维护**：steady 原始表 schema 变更时须手工 re-vendor
  `datahub/tests/fixtures/steady_raw_schema.sql` + 补 `deploy/migrations/NNN_*.sql`，与 parity 测试同 PR。
