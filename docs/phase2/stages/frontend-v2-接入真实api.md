# frontend-v2 接入真实 API（2.1）

> 阶段归档。定稿设计见 [`../design/frontend-v2-api-contract.md`](../design/frontend-v2-api-contract.md) 与 [`../design/frontend-v2-数据缺口盘点.md`](../design/frontend-v2-数据缺口盘点.md)。

## 目标

`frontend-v2/`（14 页 React 18 + TS + Vite + ECharts 纯 mock 原型）从 mock 数据切到真实后端 API。**本次明确只做前端接入**：按契约目标形态全量接入、缺字段标空态（数据诚实）、后端缺口落地后前端**零返工自动点亮**。G1~G8 后端缺口实现不在本次范围。

**前置结论（数据缺口盘点）**：Tushare 账号仅 120 积分，但 G1~G5/G7/G8 后端数据全部已产出或可推导（估值/财务走采集器内置 AkShare 兜底），缺的只是后端查询代码（**纯代码、零费用**）；G6 简报表 3 个增强字段（北向资金/两市成交/恒生+A50）未采集，补救只能走免费 AkShare（同 `hotspot.py` 架构）。

## 时间

| 起 | 止 | 说明 |
|---|---|---|
| 2026-08-23 | 2026-08-23 | 分支 `feat/frontend-v2-api`（从 dev 拉），单日完成 |

## 设计

- **API 层**：`src/api/` 纯 fetch 封装，不引 axios。`API_BASE = VITE_API_BASE ?? '/api/v1'`（相对路径，开发走 Vite proxy → `http://127.0.0.1:8080`，生产走 nginx）。信封 `{code,message,data}`：`code!==0` 抛 `ApiError`；**`/health` 无信封走 `http.raw`**。
- **G 缺口字段一律 `?:`**，形状对齐契约目标形态（`StockPoolItem`/`FactorScore`/`StrategySignal` 等扩展字段可选）→ 后端补缺口后前端零改动点亮。
- **`useApi` hook**：deps 变化重取、`reload()` 手动重跑；40004（资源缺失）→ 空态；网络/50001 → 页内 Notice + 重试。重取保留旧数据防闪烁。
- **枚举变换**（契约 §2）：`BUY/SELL/HOLD → buy/sell/hold`（`mapAction`）、`hs300/zz500 → hs/zz`（`mapUniverse`）。
- **格式化约定**：金额单位元（`fmtMoney`）；涨跌幅/收益为百分比数值（15.2=15.2%）；**回测收益为小数比例 ×100**（`fmtRatioPct`）。
- **排序白名单裁剪**：`/stocks` 后端仅支持 code/name/list_date/market/industry，前端排序下拉按后端能力裁剪（无 rank/chg/amt）。
- **空态策略**（数据诚实）：G1 分项列/G2 行情评分列 →「—」+ title 提示待 G*；G3 因子雷达 →「待 G3」；G4 名称 → `name‖code` 兜底；G5 健康卡 →「待 G5」；G6 北向/两市成交/恒生/A50 →「该能力未产出」；G7 其余服务/告警/数据资产 → 灰显「待 G7」；G8 fill_mode → 表单保留但提交不传。
- **图表**：`chartOpt.ts` 折线数据允许 `(number|null)` 断点（对齐两个日期序列），y 轴 formatter 幅度感知（`≥100 → 0 位小数`），使归一化 ~1.0 的净值序列可读。

## 实现

关键改动（均在 `feat/frontend-v2-api` 分支工作区，**尚未提交**）：

- **API 基建**：`src/api/http.ts`、`types.ts`、`transform.ts`、`src/api/{settings,stocks,signals,trade,market,ops,brief,backtest,llm,index}.ts`、`src/hooks/useApi.ts`、`src/components/Notice.tsx`、`src/lib/format.ts`、`src/vite-env.d.ts`。
- **配置**：`vite.config.ts` 加 `/api` proxy；`src/index.css` 加 `.notice`/`.muted` 样式。
- **逐页接入（9 页）**，每页独立 `npm run build` 验证：
  1. **Settings**：Tushare/飞书/LLM 三卡真实读写 + 测试，per-card Notice+重试。
  2. **Stocks**：服务端分页/搜索/排序裁剪；KPI 用服务端 total；G2 列「—」。
  3. **StockDetail**：真实 K线（`[open,close,low,high]` 喂 candlestick）+ 财务 + 信号历史；G3 雷达空态。
  4. **Signal**：策略卡用 `/strategies`（params 动态 + factor_weights）；KPI 计数用 `?action=` total；G1 列「—」。
  5. **Trade**：account+nav+positions+orders+trades；净值图 `lineOpt`；G4 `name‖code`。
  6. **Dash**：净值 vs 沪深300 双系列（账户 nav 与 `/index/nav/000300` 按日对齐，指数自动补 `sh` 前缀）；超额=两端点年化差；G5 健康卡空态。
  7. **Brief**：sections 严格映射（`market.indices`→隔夜外盘、`sectors_gain`→热点板块、`hot_stocks`→活跃个股、`yesterday.*`→昨日回顾、`today.checklist`→今日计划）；AI 解读接 `/llm/interpret-brief`；G6 空态。
  8. **Ops**：任务时间线用 `/tasks/runs`（success/skipped/failed→tl 状态，`created_at‖run_date` 兜底空串）；服务状态仅 backend/db 两行真实（/health raw），其余灰显「待 G7」。
  9. **Backtest**：列表/详情/发起接真实；收益×100；行点击拉 `nav[]` 画净值曲线；策略下拉用 `/strategies`；G8 fill_mode 提交不传。
- **保留 mock**：FactorLab / FactorFactory / StrategyFactory（数据待 2.3/2.4）；`mock/data.ts`、`random.ts` 保留被未接页面引用，`chartOpt.ts` 复用。

## 验收

- ✅ **构建**：10 步（Step1 基建 + Step2~10 九页）各自 `npm run build`（= tsc && vite build）0 类型错误（strict）。
- ✅ **后端 smoke**（后端运行于 127.0.0.1:8080，直连 curl 核对形状）：
  - `/api/v1/health` 无信封 → `{"status","time","db"}`（raw 分支验证）。
  - `/account`、`/account/nav`（nav/total_asset/daily_return/trade_date）、`/positions`（空态分支）、`/signals?page_size=1`（items 无 G1 字段 → 前端「—」）、`/strategies`（params top_n/universe/buy_buffer… 动态渲染）、`/index/nav/000300`（自动补 sh 前缀）、`/tasks/runs`（created_at 有空串 → 已兜底）、`/backtests`（收益为小数比例、list 无 nav → 行点击拉详情）、`/morning-brief`（sections 含 hot_stocks.board_days/industry）、`/stocks?page_size=2`（排序白名单吻合）。
- ✅ **无后端兜底**：所有页经 `useApi` 落 error 分支 → Notice +「重试」，不白屏。
- ⚠️ **浏览器手工 smoke 未完成**：dev server 曾拉起（proxy 配置生效），因用户停掉 5173 监听而中止；页面级渲染依赖构建 + 类型 + 接口形状三重验证兜底。

## 遗留

- **G1~G8 后端实现**（纯后端代码 + 免费 AkShare，本次明确不做）：G1 /signals 因子分项、G2 /stocks 评分池、G3 详情因子得分、G4 名称、G5 健康检查、G6 简报表 3 字段采集、G7 运维接口、G8 回测 T+1。落地后前端自动点亮，无需返工。
- **提交**：分支 `feat/frontend-v2-api` 全部改动尚未 commit；按分支纪律测试后合回 dev。
- **页面级 smoke**：下次有浏览器环境时对 9 页做一次冒烟（后端 + vite dev）。
