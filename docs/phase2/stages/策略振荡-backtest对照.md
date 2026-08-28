# 策略振荡修复 · backtest 对照验证

> 归档模板：每个阶段一页。本页是**修复前置验证**（用户拍板路径：先跑持仓重构的 backtest 对照验证再改代码），不是正式阶段归档；周末改代码后补正式归档。
> 关联：进度总表 `../../进度总表.md`（待办 · 振荡修复）· 记忆 `strategy-oscillation-fix-pending` · 策略与风控归档 [`策略与风控.md`](策略与风控.md)。

## 目标

多因子轮动策略存在振荡缺陷：实盘 `multi_factor._reconstruct_holdings` 把**上一信号日 action ∈ {BUY, HOLD} 全视为持仓**，而 HOLD 同时含「已持仓在缓冲带内」与「未持仓等回调」——后者被误计为持仓，导致 mass-HOLD 日（08-19:719、08-21:725、08-25:716）与 mass-SELL 日（08-20:704、08-24:695、08-27:686）**隔日交替**。

修复方向（用户 2026-08-27 拍板）：`_reconstruct_holdings` 改为从**真实持仓**（position/trade 表）重建，与 Go ExecuteDay 的 `ledger.positions` 同口径。

本页 = 修复前的 **backtest 对照验证**：同一窗口同参数，对照三种持仓重建模式，确认修复方向能消除振荡、且回测与实盘闭环一致。

## 对照方案

给回测引擎加 `holdings_mode`（`replay.py`，默认 `running` 不变），三种模式：

| 模式 | 持仓来源 | 对应 |
|---|---|---|
| `running` | 信号运行集（BUY 加 / SELL 删，HOLD 不改） | 回测现状 |
| `reconstruct` | 上一信号日 {BUY, HOLD}（含误计缺陷） | **实盘现状语义** |
| `portfolio` | 引擎真实组合持仓（每日信号前同步） | **修复方向**（=实盘 position/trade 重建） |

脚本 `scripts/compare_holdings_modes.py`：同窗口（默认 2024-01-01 ~ 2026-08-21，639 交易日，top_n=20，实盘参数）三模式 × 两种 fill_mode 配对跑，输出净值指标 + 每日信号计数统计（mass-HOLD/mass-SELL 天数、相邻振荡次数、SELL 信号 vs 实际 SELL 成交幻影量）。

## 结果

### fill_mode=t1_open（保守假设，无未来函数）

| 模式 | 总收益 | 年化 | 回撤 | 夏普 | turnover | 成交 | 末持仓 | massHOLD | massSELL | 振荡 | SELL信号 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| running | +7.22% | +2.79% | -15.67% | 0.268 | 21.51 | 2581 | 19 | 639 | 0 | 0 | 1434 |
| **reconstruct** | +0.21% | +0.08% | -8.84% | 0.046 | 9.02 | 1061 | **7** | **320** | **567** | **638** | **221165** |
| **portfolio** | **+9.00%** | **+3.46%** | -15.17% | 0.310 | 21.92 | 2675 | 19 | 639 | 0 | 0 | 1329 |

### fill_mode=t_close

running +4.52% / reconstruct -0.14% / portfolio +4.51%（回撤 -15.95 / -9.10 / -15.75；末持仓 22 / 7 / 21；SELL信号 1434 / 221165 / 1338）。

### 幻影 SELL 对照（t1_open）

- running：SELL 信号 1434 → 成交 511（幻影 923）
- **reconstruct：SELL 信号 221165 → 成交 345（幻影 220820，99.8%）**
- **portfolio：SELL 信号 1329 → 成交 520（幻影 809）**

### 逐日振荡样例（reconstruct，2026-06 起）

```
06-01 15 BUY / 719 HOLD → 06-02 704 SELL / 30 HOLD → 06-03 728 HOLD → 06-04 700 SELL …
```
与实盘 08-19~08-27 观察（719→704→725→695）逐位吻合。

## 结论

1. **修复方向验证通过**：`reconstruct`（实盘现状语义）完整复现振荡——638 次相邻振荡、567 个 mass-SELL 日、221165 条 SELL 信号中 220820 条是幻影（对无持仓的股票发 SELL，引擎/ExecuteDay 按真实持仓过滤不成交）。`portfolio`（修复方向）0 振荡、~1329 SELL 信号（正常换手），且 t1_open 收益更高（+9.00% vs +0.21%）。
2. **reconstruct 的「低回撤」是退化假象**：2.6 年总收益仅 +0.21%、期末持仓仅 7 只（目标 top_n=20 的近 1/3，几乎空仓）、turnover 9.02（持仓太少换不动）——不是稳健，是根本没在做轮动。其信号流对前端/决策层 99.8% 是垃圾。
3. **当前回测（running）不忠实复现实盘**：running 无振荡（SELL 信号仅 1434），振荡缺陷从未在回测中暴露 → 修复后回测须切 `portfolio` 语义，才能与实盘闭环一致（这是本次对照最重要的旁证）。
4. **风险确认**：实盘账户一旦有持仓（如 08-27 起持有 000651/601838），后续 mass-SELL 日会基于错误持仓判断误发 SELL——虽 ExecuteDay 按真实持仓过滤，但信号层面已失真，误卖风险真实存在。

## 周末改代码依据

- `quant-engine/app/strategies/multi_factor.py`：`_reconstruct_holdings` 改从 `position`/`trade` 表重建真实持仓（实盘侧）。
- `quant-engine/app/backtest/replay.py`：回测持仓模式默认切 `portfolio`（引擎同步真实组合），与实盘同口径；保留 `running`/`reconstruct` 供对照与回归。
- `quant-engine/app/backtest/engine.py`：portfolio 模式持仓同步钩子已就位（本次已实现）。
- 测试 `tests/test_holdings_modes.py`：三种模式语义已锁定（5 用例）。
- 附带同批：601162 天风证券 null-roe 回退（factor 取最新非空 roe）。
- 对照脚本 `scripts/compare_holdings_modes.py` 保留，供改后回归验证。

## 遗留

- dev DB `factor_definition` 缺 2.3b 列（version/status/params）、`strategy` 缺 zh_name/version/updated_at——GORM AutoMigrate 只在 prod 跑过，dev 落后。对照脚本已用列级 select 规避；正式修复前建议补一次 dev 迁移对齐。
- 振荡量化指标（mass-HOLD/mass-SELL/交替次数）目前只在对照脚本，可考虑固化进 data_quality 或策略监控（待周末改代码时一并决策）。
