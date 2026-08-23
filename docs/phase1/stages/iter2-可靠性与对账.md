# Iteration 2：可靠性与对账

## 目标

保证"数据可信、账是平的、跑了不丢"：数据健康检查（数据侧）、交易一致性对账（交易侧）、调度缺口补齐（重启补跑 + 每晚回填）。对应 Issue #17（交易一致性校验）/ #18（补数据/调度缺口）。

## 时间

| 起 | 止 | 说明 |
|---|---|---|
| 2026-08-22 03:17 | 2026-08-22 04:20 | 按 commit 日期 |

## 设计

1. **数据健康检查（数据侧）**：`quant-engine/app/data_quality.py`，18:30 由 `job_data_quality` 触发（`tasks.py:209`）。**7 项检查**（`data_quality.py:62-252`）：
   - 行情覆盖率 / 缺失交易日 / 重复数据 / 价格异常 / 估值滞后 / 财务新鲜度 / 指数基准完整
   结果写 `task_run` 台账（`tasks.py:178-189`），18:35 推飞书（notify_config `data_quality` 事件）。
2. **交易一致性对账（交易侧）**：`backend/internal/service/consistency.go`，21:15 由 `sched.RegisterCatchUp("consistency-check", 21, 15, ...)` 触发（`main.go:84`）。对以下做**独立复算**（不信任业务写入，重算比对）：
   - 现金 + 市值 = 总资产
   - 订单 = 成交（每笔 FILLED 有对应 trade）
   - T+1 可卖数（available_qty ≤ quantity，当日买入冻结）
   - 净值幂等（account_nav 当日唯一）
   - 自动交易不重复执行（当日净值已存在则跳过）
   结果写 `task_run`，**对账通过 / 未通过均推飞书卡片**。
3. **调度缺口补齐（Issue #18）**：
   - **backend Scheduler 重启同日补跑**：`scheduler.go:66-78,113-142`——19:35 时进程不在线，重启后检测当日未执行则补跑（collector 侧同样有补跑语义）；
   - **collector 每晚自动回填**：夜间回填补历史日线（到 2016-08-01）+ 补估值滞后股，带交易日门控（`is_open != 1` 跳过）。

## 实现

| 主题 | 提交 | 内容 |
|---|---|---|
| 交易一致性校验 | `629c88d` | 每日对账 + 飞书卡片，打通交易闭环（#17） |
| 补数据/调度缺口 | `f1c2010` | Scheduler 重启同日补跑 + collector 每晚自动回填（#18） |

关键产物：

- 数据质量：`quant-engine/app/data_quality.py`（7 项检查 + 结果写台账）
- 一致性对账：`backend/internal/service/consistency.go`（5 项独立复算 + 飞书卡片）
- 重启补跑：`backend/internal/service/scheduler.go:66-78,113-142`（含 collect 补跑）
- 夜间回填：`collector/app/tasks.py`（`backfill` / `backfill-valuation`）

## 验收

- ✅ 7 项数据健康检查全部落地，结果落 `task_run` 台账，18:35 推飞书
- ✅ 一致性对账 5 项独立复算，通过/未通过均推飞书卡片
- ✅ Scheduler 重启后同日补跑（backend 自动交易）
- ✅ collector 每晚自动回填（历史日线到 2016-08-01 + 估值滞后股，交易日门控）
- ✅ 任务失败红色告警（Iteration 1 已有，本迭代数据质量/对账结果接入同一告警通道）
- ⚠️ 对账/健康检查覆盖的是"交易与数据"主线；quant-engine 的 19:00 因子 / 19:30 信号**无重启补跑**（进程不在线只能靠 CLI 手动或等"未执行"告警）——已在遗留记录

## 遗留

1. **数据库备份恢复验证** 🚧：备份脚本已在跑（压缩到 VM 本机），但**恢复演练未做**——这是 Iteration 2 唯一未闭环项
2. **quant-engine 无重启补跑** 🚧：因子/信号定时任务若进程不在线不会自动补跑，只能 CLI 手动（`python -m app.cli factors/signals --date`）或等"该做没做"告警后处理
3. 异地备份（Iteration 0 遗留）仍未做，与备份恢复演练可合并安排
