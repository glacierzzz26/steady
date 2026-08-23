# 回测可信度校准（Iteration 3 · G8 T+1 成交假设）

> 第二阶段研究地基：消除回测未来函数（look-ahead bias），使回测结论可信、结果可复现。
> 状态：📋 定稿（2026-08-23）· 用户确认后登记 `design/README.md` 并按此执行。
> 关联：[接口契约 G8](./frontend-v2-api-contract.md#52-第二阶段22-回测可信度校准iteration-3--研究地基) · [优化路线图 优先级三](../../优化路线图.md) · [进度总表](../../进度总表.md)

## 1. 背景与目标

回测是研究闭环的信任地基——「回测收益好看」没有意义，除非证明它**不是未来函数假象**、且**同参数可复现**。本迭代完成三件事：

1. **消除未来函数**：信号 T 日收盘生成，成交改为支持 T+1 开盘价（保守假设），提供与现状 T 日收盘成交的对比。
2. **偏差可量化**：每个回测结果带 `t1_deviation`（T vs T+1 年化偏差），回测页展示「偏差」KPI；产出 T vs T+1 偏差报告。
3. **结果可信**：固定 fixture 边界测试 + 同参可复现性测试，建立回测结果的可信基线。

## 2. 现状（2026-08-23 已核实代码）

| 环节 | 现状 | 位置 |
|---|---|---|
| 成交时点 | 信号 T 日收盘生成，**T 日收盘价成交**（`_get_price` 取 close） | `quant-engine/app/backtest/engine.py` |
| 信号生成 | MA/MACD 因果算子、估值 as-of（≤T 30 天）、财务 announce_date ≤T——信号本身 T 收盘后可得，**无内部未来函数** | `replay.py` |
| 因子 warmup | 区间前 120 天预热（MA20/EMA26） | `replay.py: PRICE_WARMUP_DAYS` |
| T+1 冻结 | `_unfreeze_t1` 每日全部解冻：T 买入 → T+1 可卖（与 A 股 T+1 一致） | `engine.py:88` |
| 涨跌停/税费 | 分板块涨跌停、印花税万5（卖）、佣金万2.5/最低5、滑点 0.1% | `broker.py` |
| 开盘价 | `daily_price.open` **列已存在且已采集**（Tushare 含 OHLC） | `tables.py:46`、collector |
| 任务链路 | `POST /backtests` → Go 校验建 job → quant-engine `consume_pending` 消费 → `run_and_save` 落 `backtest_result` | `backtest.go`、`backtest_service.py` |
| 幂等唯一键 | `(strategy_name, start_date, end_date, top_n)`——**不含成交假设** | `init.sql:302`、`backtest_service.py:53` |
| DDL | `deploy/postgres/init.sql`；**无迁移机制**，部署库需手工 ALTER | `init.sql:290-319` |
| 前端 | Backtest 页已有「T日收盘/T+1开盘」分段 + `G8_HINT` 占位；`types.ts` 已预留 `fill_mode?/t1_deviation?/turnover?`，**提交时不传、展示空态** | `frontend-v2/src/pages/Backtest/index.tsx`、`api/types.ts:316` |

### 核心风险点（本迭代要消除的）

`engine.py run()` 每日循环：`strategy.run(T)` 用 T 收盘信息算出信号 → 立即以 **T 收盘价** 成交。
现实里 T 收盘那一刻才知道信号、已无法以该收盘价成交 → **look-ahead bias**。
行情类因子（MA/MACD）尤其受影响；财务/估值已用 announce_date/as-of 部分防御。

## 3. 设计决策

### 3.1 fill_mode 语义

| fill_mode | 信号时点 | 成交时点 | 性质 |
|---|---|---|---|
| `t_close` | T 日收盘后 | **T 日收盘价** | 乐观假设（含 look-ahead），**默认值，兼容现状与历史结果** |
| `t1_open` | T 日收盘后 | **T+1 日开盘价** | 保守假设（无未来函数），前端默认切到它 |

- 引擎确保 t1_open 下 `signal_time(T 收盘) < fill_time(T+1 开盘)`。
- **默认 `t_close`**：API 与 DB 默认值，历史 job 语义不变、结果可复现；前端 UI 默认选 `T+1开盘`（引导保守口径），不强制改 API 默认。

### 3.2 引擎改造（quant-engine）

`BacktestEngine(strategy, start, end, db, fill_mode="t_close")`：

1. **成交价口径**：`_get_price` 拆出 `_get_fill_price(code, date)`——`t_close` 返回当日 close，`t1_open` 返回当日 **open**。t1_open 下 T+1 无 open（停牌/数据缺失）→ 跳过该信号成交（与停牌同处理）。
2. **延迟成交队列**：t1_open 时，T 日信号不入队成交、暂存 `pending_signals`；T+1 日循环先执行昨日 pending（以 T+1 open），再生成 T 日新信号入队。t_close 行为不变（当日信号当日成交）。
   - 首日：无 pending → 不成交；末日信号：不入队执行（窗口外无法成交，天然成立）。
   - T+1 冻结语义自动成立：T+1 开盘买入当日冻结（available=0）→ T+2 解冻可卖，与 `_unfreeze_t1` 顺序兼容（先解冻、后执行 pending）。
3. **涨跌停判断**：t1_open 下对 T+1 开盘价 vs T 收盘价（`_get_prev_close` 已返回前一交易日收盘，逻辑复用不变）。
4. **ReplayStrategy 补充 open**：preload 的 `select` 增加 `open` 列，缓存 `opens: {code: {date: open}}`，新增 `open_at(code, date)` 供引擎取价（`price_at` 保持 close 语义不变）。
5. **偏差配对运行**：同一 job 引擎跑两遍（primary + 另一模式），**共享一次 preload**——preload 数据（`series/grid/pool`）是只读的，浅拷贝给第二个策略实例即可，第二个实例持有独立 `holdings` 状态，避免状态串扰；代价 ≈ 1× 数据加载 + 2× 内存循环，可接受。

### 3.3 t1_deviation 口径

- `t1_deviation = annualized_return(t1_open) − annualized_return(t_close)`（年化收益百分点，保留 4 位）。
- 任意 fill_mode 的 job 都填 `t1_deviation`（引擎两遍都跑了），`nav`/指标组存 **primary fill_mode** 的。
- 偏差为正 → T+1 反而更好；为负 → T 日收盘成交高估了收益（未来函数嫌疑的量化证据）。

### 3.4 偏差报告（交付物）

CLI 子命令 `python -m app.cli backtest-deviation --start --end --top-n [--strategy multi_factor]`：
跑一组窗口 × 两种 fill_mode，输出对比表（窗口/总收益(t_close)/总收益(t1_open)/年化偏差/成交笔数差异），并可选写回一份 markdown。作为 Iteration 3 归档的偏差报告原始数据。

### 3.5 固定 fixture 边界测试 + 可复现性

沿用 `quant-engine/tests/test_backtest.py` 的 sqlite 内存 fixture（现为 close-only），**补 open 列**，覆盖边界用例（见 §6 用例表）：

- 可复现性：同 fill_mode 同参数同区间两次 → nav 序列与报告逐字段一致（扩展现有测试）。
- t1_open 语义 + 边界：T+1 开盘成交价、末日信号不成交、涨停买不进、跌停卖不出、现金不足 100 股递减、T+1 卖出限制、空数据区间、停牌期间持有。
- **回归**：t_close 默认路径行为不变（现有 `test_backtest.py` 全绿）。

## 4. 接口契约变更

### 4.1 `POST /backtests`（扩展）

```jsonc
// 请求体
{ "start_date": "2019-01-01", "end_date": "2026-08-20", "top_n": 20,
  "fill_mode": "t1_open" }        // 可选，默认 t_close，枚举校验
// 成功响应不变
{ "code": 0, "data": { "job_id": 1, "status": "pending" } }
```

### 4.2 任务列表 / 详情 DTO（扩展）

`backtestJobDTO` 增加（`omitempty`，旧数据无值则省略 → 前端空态）：

```jsonc
"fill_mode": "t1_open",       // job 的成交假设
"t1_deviation": -0.0234,      // 年化偏差（任意 fill_mode 都填）
```

### 4.3 通用约定

- `fill_mode` 枚举 `"t_close" | "t1_open"`，后端小写，前端分段组件提交小写值（沿用 signal action 的大小写约定）。

## 5. 数据库变更

> 无迁移机制 → **新装走 init.sql，部署库手工 ALTER**（本迭代给出 SQL）。

**init.sql（新装）**：
```sql
-- backtest_job 增加 fill_mode；唯一键纳入（否则同参数两种假设互相去重）
ALTER 语义合入建表：fill_mode VARCHAR(16) NOT NULL DEFAULT 't_close'
uq_backtest_job (strategy_name, start_date, end_date, top_n, fill_mode)

-- backtest_result 增加
fill_mode     VARCHAR(16) NOT NULL DEFAULT 't_close',
t1_deviation  DECIMAL(10,4)
```

**部署库迁移 SQL（执行时贴在归档里）**：
```sql
ALTER TABLE backtest_job   ADD COLUMN IF NOT EXISTS fill_mode VARCHAR(16) NOT NULL DEFAULT 't_close';
ALTER TABLE backtest_result ADD COLUMN IF NOT EXISTS fill_mode VARCHAR(16) NOT NULL DEFAULT 't_close';
ALTER TABLE backtest_result ADD COLUMN IF NOT EXISTS t1_deviation DECIMAL(10,4);
DROP INDEX IF EXISTS uq_backtest_job;
CREATE UNIQUE INDEX uq_backtest_job ON backtest_job (strategy_name, start_date, end_date, top_n, fill_mode);
```

**同步改动**：`quant-engine/app/models/tables.py`（BacktestJob/BacktestResult 加列）、`backend/internal/model/backtest.go`、`backend/internal/repository/backtest_repo.go`（CreateJob 透传 fill_mode）、`backtest_service.py create_job`（values + on_conflict 索引加 fill_mode）。

## 6. 测试计划（fixture 边界用例表）

fixture：sqlite 内存，价格含 open/close/adj_factor，含 warmup 段。以下每条一个测试：

| # | 场景 | 断言 |
|---|---|---|
| 1 | 可复现性（t_close 与 t1_open 各自） | 同参两次 nav/报告逐字段一致 |
| 2 | t1_open 成交时点 | T 日信号成交于 T+1，成交价 = T+1 open |
| 3 | 末日信号不成交 | 最后一交易日信号无对应成交记录 |
| 4 | 涨停买不进 | T+1 open ≥ T close×(1+limit) → BUY 跳过 |
| 5 | 跌停卖不出 | T+1 open ≤ T close×(1−limit) → SELL 跳过 |
| 6 | 现金不足 | 100 股递减重试，减到 0 放弃；成交笔数/持仓符合 |
| 7 | T+1 卖出限制 | T+1 开盘买入当日不可卖（available=0） |
| 8 | 空数据区间 | 无 price/无信号 → 报告不崩，nav 恒为初始资金 |
| 9 | 停牌期间持有 | T+1 无 open → 跳过成交，持仓继续 mark-to-market |
| 10 | t_close 回归 | 默认路径与改造前结果一致（现有测试全绿） |

## 7. 前端变更

- Backtest 页：`fillMode` 默认 `'T+1开盘'`；提交带 `fill_mode`（映射 `T日收盘→t_close` / `T+1开盘→t1_open`）；去掉 G8_HINT「提交时不传」提示。
- 历史表新增「假设」列（`fill_mode` 展示 t_close/t1_open）；详情 KPI 行新增「偏差」卡（`t1_deviation` → 带符号百分比，未产出显示 `--`）。
- `types.ts` 字段已预留，直接点亮，无结构改动。

## 8. 交付物与验收

- [ ] 引擎支持 `fill_mode`，t1_open 无未来函数（signal_time < fill_time 有测试佐证）
- [ ] `backtest_job/result` 落 fill_mode + t1_deviation，接口透传
- [ ] 前端默认 T+1 开盘、提交带参数、历史/详情展示假设 + 偏差 KPI
- [ ] fixture 边界测试 + 可复现性测试全绿；t_close 回归通过
- [ ] 偏差报告（CLI 跑一组窗口输出对比表）
- [ ] smoke test：dev 环境两种 fill_mode 各提交一个任务跑通

## 9. 范围边界（本迭代不做）

- **换手率/交易成本分析**（`turnover` 字段）→ Iteration 4 策略与风控
- 信号日内时点细化（盘中/收盘再细分）→ 本设计固定「收盘生成」
- 撮合细节（订单簿/部分成交）→ 不引入
- 15-min 历史回测 → 路线图已标暂不做
- G9/G10/G11（FactorLab/FactorFactory/StrategyFactory）→ 2.3/2.4

## 10. 执行步骤

1. 拉分支 `feat/backtest-t1`（从 `dev`）
2. 引擎改造：fill_mode + open 缓存 + 延迟成交队列 + 配对运行（§3.2）
3. 后端：model/repo/service/handler + init.sql + 部署迁移 SQL（§4/§5）
4. fixture 测试 + 可复现性 + 回归（§6）
5. 前端点亮（§7）
6. 偏差报告 CLI + 跑一组窗口（§3.4）
7. 合回 `dev` → 归档 `phase2/stages/回测校准.md` → 更新进度总表 + 契约 G8 勾选
