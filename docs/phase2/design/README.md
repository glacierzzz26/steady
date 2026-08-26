# 第二阶段 · 定稿设计索引

> 本阶段定稿的设计文档放这里。约定与格式沿用 `../../phase1/design/README.md`。

## 定稿设计

| 文档 | 状态 | 定稿日期 | 说明 |
|---|---|---|---|
| [frontend-v2 接口契约](frontend-v2-api-contract.md) | 📋 定稿 | 2026-08-23 | 14 页 × 数据需求 → API 映射；现有接口契约；G1~G11 缺口清单与归属阶段（2.1~2.4）；执行状态跟踪表。第二阶段执行蓝本，按缺口逐项落地勾选。 |
| [frontend-v2 数据缺口盘点](frontend-v2-数据缺口盘点.md) | 📋 定稿 | 2026-08-23 | 数据层面核实：各页缺字段×缺失程度×后台数据能否满足×补救办法×是否收费。**结论：120 积分下 G1~G5/G7/G8 数据全部在库（估值/财务走 AkShare 兜底），缺的只是后端查询代码（零费用）；G6 简报表 3 个增强字段未采集，补救只能走免费 AkShare。** 契约 G1~G11 的数据可行性附录。 |
| [回测可信度校准（G8）](backtest-t1-可信度校准.md) | 📋 定稿 | 2026-08-23 | Iteration 3 设计：fill_mode(t_close/t1_open) 消除未来函数、t1_deviation 偏差口径、配对运行、fixture 边界测试与可复现性、前端点亮。执行蓝本，落地后勾选契约 G8。 |
| [策略与风控（G11）](策略与风控.md) | 📋 定稿 | 2026-08-23 | Iteration 4 设计：策略生命周期（多策略/状态机/单 active/fork）、权重口径切换（策略因子级权重三处同步）、回测多策略 + A/B 对比、风控落执行层（止损/熔断/行业集中，Go 实盘 + Python 回测同口径）、turnover/cost 风险指标、StrategyFactory 前端接通。执行蓝本，落地后勾选契约 G11。 |
| [因子研究闭环（G9+G10）](因子研究闭环.md) | 📋 定稿 | 2026-08-25 | 2.3 因子研究：IC/ICIR/分层/衰减/相关性（FactorLab）+ 因子 CRUD/版本/试算/寻优（FactorFactory）。核心决策：IC 统计数学 Python 单实现（`factor_stat`/`factor_corr` 预计算表），Go 只读表+轻聚合，避免两套实现漂移。试算=已有因子参数化重算。执行蓝本，落地后勾选契约 G9/G10。 |
| [数据源评估：BaoStock 迁移预案](数据源评估-BaoStock.md) | 📋 评估+阶段1 | 2026-08-26 | **数据稳定性评估 + 迁移预案（阶段 1 已实施，2026-08-27）**。根因：东财 82.push2 双端封锁致 daily/valuation/finance 主源静默降级/缺数。实测：BaoStock 在 dev + 生产双端登录/日线+估值/指数/日历/财务全通，无 token 无限额。结论：BaoStock 做 A 股基本面+日线核心新主源，AkShare 只保留 hotspot/外盘层，Tushare 降兜底。最高风险=复权口径切换（无 adj_factor 列，需派生）。**阶段 1 已切 daily+calendar（OHLCV 走 BaoStock、adj_factor 保留 Tushare），归档 [`../stages/BaoStock迁移-阶段1.md`](../stages/BaoStock迁移-阶段1.md)；阶段 2 valuation/finance/index/stock_basic 待续**。 |
