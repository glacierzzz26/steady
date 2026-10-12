# datahub Phase 3 · 生产切换 runbook（全量接入）

> **状态：📝 只写不执行**。本文件是「steady 原始数据全量接入 datahub」的生产操作手册；
> 实际执行须人工择时、逐数据集放行，**AI 不执行生产操作**。
> 蓝图 [`../design/数据接入层-datahub-phase3.md`](../design/数据接入层-datahub-phase3.md)；
> 增量叙事 [`datahub-phase3-读取切换.md`](datahub-phase3-读取切换.md)；工具 PR（datahub 仓）`scripts/seed_from_steady.sh` + `scripts/reconcile_table.py`。

## 0. 前置事实

- **四个数据集代码已就位**（qe + backend 均切读，闸门默认关）：`stock_basic`、`daily_valuation`、
  `financial_indicator`、`daily_price`（**含 index**——指数行情物理存在 `daily_price`，code=`sh000300` 等）。
- **datahub 库与 steady 库同处一个 PG 实例**（容器 `quant-postgres`，源库 `quant_system` → 目标库 `datahub`）
  ⇒ 历史底种走**直接库拷贝**（`pg_dump --data-only | psql`），逐位精确、分钟级、零对账漂移。
- 采集白名单 id 与 raw 数据集 id **不同名**（见下表，勿混用；`DATAHUB_COLLECT_DATASETS` 用前者，
  `DATAHUB_READ_DATASETS` 用后者）。
- 每个数据集「停 steady 采集」是**最后一步**，须 **quant-engine 与 backend 都已切读**（否则 backend 服务陈旧数据）。

## 1. 数据集清单与顺序

| # | raw 数据集 id（读闸门值） | datahub 采集 id（采集闸门值） | steady 停采 job | 行数（拷贝后应一致） | 备注 |
|---|---|---|---|---|---|
| 1 | `stock_basic` | `stock_basic` | `job_sync_stock_list` | 5,554 | **FK 父表**（含 `market='INDEX'` 伪行）；**无 BIGSERIAL**（不 setval） |
| 2 | `daily_valuation` | `valuation` | `job_sync_valuation` | 9,279,654 | BIGSERIAL，需 setval |
| 3 | `financial_indicator` | `finance` | `job_sync_finance` | 34,919 | BIGSERIAL，需 setval |
| 4 | `daily_price`（含 index） | `daily` **+ `index`** | `job_sync_daily_price` **+ `job_sync_index`** | 10,262,010 | BIGSERIAL；行数最大、读点最多、**最后切**；采集器有「`adj_factor` 全 NULL 不覆盖」守卫 |

顺序：**`stock_basic → daily_valuation → financial_indicator → daily_price`**。逐数据集**串行**（前一个观察通过再动下一个）。

## 2. 每数据集 6 步

> 记 `<D>` = raw 数据集 id、`<C>` = 采集 id、`<J>` = steady 停采 job、`<SEED>` = 底种表（=`<D>` 对应表名）。

### Phase A — datahub 侧（先）

1. **发布 datahub 镜像**（含该集 raw 数据集 + 采集器）。生产用 `datahub:v0.0.0-<短hash>`（未发版恒 `v0.0.0`）。
2. **备份**：`cd <datahub 部署目录> && cp .env .env.pre-phase3-<D>-$(date +%Y%m%d)`。
3. **采集放闸**：`.env` 置 `DATAHUB_COLLECT_ENABLED=1` 并把 `<C>` 加入 `DATAHUB_COLLECT_DATASETS`
   （`daily_price` 需同时含 `daily` 与 `index`）→ 重启 `datahub-collector`。
4. **底种**（幂等，可重跑）：
   ```bash
   # 单集
   SEED_TABLES="<SEED>" ./scripts/seed_from_steady.sh
   # dry-run 先看将执行的 SQL
   DRY_RUN=1 SEED_TABLES="<SEED>" ./scripts/seed_from_steady.sh
   ```
   脚本会：`TRUNCATE datahub.<SEED> CASCADE`（**只锁目标库**）→ `pg_dump --data-only` 源表 → `psql` 导入
   （COPY 显式列名）→ 三张 BIGSERIAL 子表 `setval(MAX(id)+1,false)`（`stock_basic` 无序列、自动跳过）。
5. **对账放行门**（`--mode copy`，**逐位全等，容差恒 0**）：
   ```bash
   # stock_basic（小表，全量）
   DB_NAME=datahub STEADY_DB_NAME=quant_system python scripts/reconcile_table.py \
     --table stock_basic --key code \
     --vals name,market,industry,list_date,status,universe,data_scope --all
   # daily_price（锚定同一 end，窗口比对；全区间放行用 --all）
   DB_NAME=datahub STEADY_DB_NAME=quant_system python scripts/reconcile_table.py \
     --table daily_price --key code,trade_date \
     --vals close,adj_factor,turnover_rate --date-col trade_date --all
   # daily_valuation / financial_indicator 同理（--vals 按需；financial --date-col report_date）
   ```
   **门**：任一 `drifted / db_anomaly / false_pos / rejected` ⇒ 退出码 1 ⇒ **不放行**，排查拷贝 bug 后重跑。
   全 `accepted` 才进 Phase B。

### Phase B — steady 侧（后）

6. **发布 steady 镜像**（含读切换代码，已就位）。生产用 `steady-<date>-<短hash>`；镜像烘焙 `APP_VERSION` + `GIT_SHORT`。
7. **备份**：`cd <steady 部署目录> && cp .env .env.pre-readgate-<D>-$(date +%Y%m%d)`。
8. **翻读闸门**：`.env` 把 `<D>` 加入 `DATAHUB_READ_DATASETS`（逗号分隔；**先不动 `COLLECTOR_DISABLED_JOBS`**）
   → `docker compose up -d --force-recreate quant-engine backend`（**两个消费方同时翻**）。
9. **验证**：
   - quant-engine/backend 容器内 `DATAHUB_READ_DATASETS` / `DATAHUB_BASE_URL` / `DATAHUB_TOKEN` 生效；
   - datahub 侧访问日志出现 `GET /v1/datasets/<D>`；
   - 同 job/接口结果与翻闸前**逐位一致**（stock_basic→池/名称；daily_price→K 线/指数条/回测）。
10. **（可选）回采校验**：证明「采集器产物 == 拷贝值（容差内）」——
    ```bash
    DB_NAME=datahub STEADY_DB_NAME=quant_system python scripts/reconcile_table.py \
      --table <SEED> --key <key> --vals <vals> --date-col <date-col> \
      --limit 5 --mode recollect --numeric-tol close:0.005
    ```
    （仅近端窗口、仅数值列容差；**不得**用 recollect 替代 Phase A 的 copy 放行门。）

### Phase C — 观察 + 停采

11. **观察一个完整交易日**：datahub 当日增量 == steady 同日产物（`task_run` / 行数 / 抽样值）。
    `daily_price` 额外核对指数行（`sh000300`）与复权因子连续性。
12. **停 steady 采集**：`.env` 把 `<J>`（`daily_price` 再加 `job_sync_index`）加入 `COLLECTOR_DISABLED_JOBS`
    → 重启 collector。日志应见「停采闸门：跳过注册 <J>」；补跑探针亦跳过 ⇒ **datahub 成为该集唯一采集方**。

## 3. 回退（一键）

- **紧急回退读**：steady `.env` 清空 `DATAHUB_READ_DATASETS`（或去掉 `<D>`）→ 重启 `quant-engine` + `backend`
  ⇒ 立刻回本地读。**本地库、collector 采集器均不删**（回退与观察期兜底）。
- **撤销停采**：steady `.env` 去掉 `COLLECTOR_DISABLED_JOBS` 里的 `<J>` → 重启 collector
  ⇒ steady 恢复采集（与 datahub 并行双采，须尽快二选一，勿长期并行）。
- **中窗口兜底**：闸门开但 datahub 读失败时，quant-engine/backend 可临时 `DATAHUB_FALLBACK_LOCAL=1` 走本地
  （**仅首窗口应急**，默认 `0`=失败即抛，不静默给旧值）。

## 4. 风险 / 注意

1. **`latest_trade_date` 指数过滤不一致**：qe 排 `sh%`、backend 不排 → datahub 侧以 `exclude_index` 参数化，逐字保语义（Go 传 `false`）。
2. **序列 + FK 顺序**：`stock_basic` 先灌；三张 BIGSERIAL 表 `setval` 必做，漏则后续采集 insert 主键冲突。
3. **`daily_price` 采集器「全 NULL `adj_factor` 不覆盖」守卫**：拷贝保留 steady 因子值；回采校验时该守卫防误清。
4. **Tier2 等价性**：`GetPoolMarket`/`GetIndexQuotes`(`last=2`)、`GetSignalChg20`(`last=21`) 的 Go 归约须与原 SQL 逐行等价——翻闸后抽样核对。
5. **Tier3 遗留**：backend `GetByDate`/`GetPrevClose`、qe `backtest/engine` 逐 (code,date) 读、`data_quality.{missing_days,price_anomalies,coverage prev}` 仍走本地——仅「停本地表」的 Phase 4 才需重构，本轮不动。
6. **过渡期告警断链**：datahub 写自有库 `task_run` → steady `notify_scheduler` 不读，暂看 datahub-collector `/healthz`。
7. **跨仓发布顺序**：Phase A（datahub）必须先于 Phase B（steady）——datahub 未就绪就翻读闸门 ⇒ 读失败即抛。
