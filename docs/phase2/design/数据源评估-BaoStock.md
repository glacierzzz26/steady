# 数据源迁移评估：BaoStock 作为基本面+日线核心新主源

> 定位：数据源稳定性评估 + 迁移预案。**评估完成（2026-08-26，dev + 生产双端实测），实施待后续迭代**。
> 关联：进度总表 `../../进度总表.md`（待办登记）· 数据缺口盘点 [`frontend-v2-数据缺口盘点.md`](frontend-v2-数据缺口盘点.md) · G6 采集可行性注记（契约 §5.1）。

## 1. 背景与动机

「数据不稳定」的根因不是玄学，是**一处具体依赖被封锁**：东财 `82.push2.eastmoney.com`（及周边 push2 子域）从 **dev 和生产两端都 `RemoteDisconnected`**（服务器主动断连，与本机网络无关，属地域/反爬封锁）。而这个子域恰好是三个核心采集器的数据源：

| 采集器 | 主源 | 影响 |
|---|---|---|
| `collector/app/collectors/daily.py` | `ak.stock_zh_a_hist`（东财日线） | 退化新浪兜底（慢/偶发失败） |
| `collector/app/collectors/valuation.py` | `ak.stock_value_em`（东财估值） | **唯一源、无兜底** → 东财一断 PE/PB/市值即缺 |
| `collector/app/collectors/finance.py` | `ak.stock_yjbb_em`/`zcfz_em`（东财财务） | 靠 Tushare 兜底（有 120/min 限额） |

生产 60h 日志无报错 = **静默降级/缺数**（估值/财务表陈旧但不炸任务），正是「数据不稳定」的直接来源。辅助痛点：Tushare 免费档 `120 积分/分钟` 日频限额，回填/按股场景易超限。

## 2. 实测证据（2026-08-26）

BaoStock（`pip install baostock`，v0.9.30；自定义 TCP 协议，免费无 token）在 **dev 与生产 collector 容器内双端实测通过**：

| 能力 | 实测 |
|---|---|
| 登录 | dev/prod 均 `login: 0 success` |
| 日线+估值一查给 | `date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST`（浦发 2026-08 17 行） |
| 指数 | `query_history_k_data_plus("sh.000300", ...)` 沪深300 ✅（上证/深成/上证50/中证500/创业板同类） |
| 交易日历 | `query_trade_dates` ✅ 含 `is_trading_day` |
| 财务 | `roeAvg`(ROE)、`YOYNI`(净利增速)、`gpMargin`(毛利率)、`liabilityToAsset`(负债率)、`pubDate`(公告日) ✅ |
| 股票列表 | `query_stock_basic` ✅（code/code_name/ipoDate/status） |

对比同一环境的东财：`82.push2.eastmoney.com` http=000 / RemoteDisconnected，`push2.eastmoney.com`（kamt）http=200 但数据为已停披的北向。**BaoStock 在两端可达性是质变。**

## 3. 替换对照（按系统表）

| 系统表/需求 | 现源 | BaoStock 覆盖 | 结论 |
|---|---|---|---|
| `daily_price` | 东财→新浪 | OHLCV/量额 ✅；`adj_factor` **无直接列，需推导** | ✅ 可替换（1 处推导） |
| `daily_valuation` | 东财 | `pe_ttm`/`pb` ✅（peTTM/pbMRQ）；市值=close×totalShare；`pe_static` 无直接源 | ✅ 可替换（2 字段推导） |
| `financial_indicator` | 东财+Tushare | roe/净利增速/毛利率/负债率/公告日 ✅；营收增速需两期 MBRevenue | ✅ 可替换（1 字段推导） |
| `trade_calendar` | 新浪 | ✅ | ✅ |
| `index` | 新浪 | 主要 A 指数 ✅ | ✅ |
| `stock_basic` | 新浪/东财 | code/name/ipoDate ✅；**industry 无**（现从东财 yjbb 补） | ⚠️ industry 留 AkShare |
| `market_hotspot` | 东财/同花顺 | ❌ 无板块/资金流/人气/涨停池 | ❌ 保留 AkShare |
| 美股/恒生/A50/北向（G6） | 新浪/东财 | ❌ 纯 A 股 | ❌ 保留 AkShare |
| 两市成交（G6） | — | `sum(amount)` 派生（同一次日线数据） | ✅ 顺带解决 G6 一项 |

**边界结论**：BaoStock 只能替换「A 股基本面 + 日线历史」这一层；**市场热点（板块涨幅/资金流/人气/涨停池）、外盘（美股/恒生/A50/北向）必须保留 AkShare（东财/同花顺/新浪）**。目标架构 = **BaoStock 做基本面+日线核心新主源，AkShare 只保留舆情/外盘层，Tushare 降为纯兜底（去 token 依赖）**。

## 4. 关键风险（实施前必须确认）

1. **复权语义（最大迁移风险）**：BaoStock 给前/后复权价（adjustflag），**不给 adj_factor 列**。系统因子研究（`factor_research.py` `adj_close = close × adj_factor`）与回测全依赖该比值。方案 A：存原始 close + 派生 `adj_factor = hfq/raw`（每次拉取多一次 hfq 查询，hfq 与不复权同 range 一次拿两列再算）；方案 B：整体切后复权价。**口径必须 collector/factor_service/factor_research/backtest 四处一致**，选 A 兼容现表结构、改动最小。
2. **字段单位校准**：实测浦发 `liabilityToAsset=0.009182`，与 `1-1/assetToEquity≈0.918` 差 100 倍——负债率值单位/口径需对着真实报表读一次再落库。
3. **BaoStock 自身稳定性**：免费社区服务，高峰偶发慢、数据约 18:00 后更新（**昨日数据次日早盘可用 ✅**，满足 08:45 简报表）。但无限额、无地域封锁，且采集本就是逐股循环（现东财/新浪也是逐股，**调用形状不变**），个人规模（~5000 股 + 每日增量）无压力。
4. **回填规模**：BaoStock 无「按日全市场快照」接口（Tushare `daily` 有），2 年回填 = ~5000 股串行会慢，需**并行/分批**——迁移里唯一要新写的工程点。
5. **北交所覆盖**：BaoStock 支持 bj 代码（bj.43/83/87…），但覆盖列表需回填时用 `query_stock_basic` 全量核对一遍（现系统 stock_basic 含 SH/SZ/BJ）。

## 5. 迁移路径（实施时按此推进）

- **阶段 1（止血 + 对账）**：新增 `collector/app/sources/baostock.py` 适配层（**复刻 `sources/tushare.py` 的「同形状输出」模式**——上层 build_rows/save/清洗/重试不动，采集器只换主源、AkShare/Tushare 保留兜底）。先切 `daily` + `calendar` 两个采集器，dev 库回填后与现有 Tushare 数据**逐位对账**（同 code 同日 close/adj_factor/pe_ttm 一致），确认复权与字段口径后放行。
- **阶段 2**：`valuation`/`finance`/`index`/`stock_basic`(保留 industry 来源) 逐个切换 → 生产回填 + 上线。hotspot/外盘层不动。
- **阶段 3（可选）**：Tushare 降为纯兜底，去掉 token 与 120/min 依赖；G6 两市成交顺带用 BaoStock `sum(amount)` 点亮。

工作量约一个迭代量级；最高风险在复权口径切换，故阶段 1 先做对账再放大。

## 6. 开放问题清单（实施启动时逐项确认）

- [ ] 复权口径选 A（派生 adj_factor）还是 B（切后复权价）——按阶段 1 对账结果定
- [ ] `liabilityToAsset` 单位校准（×100 或改用 1-1/assetToEquity）
- [ ] `total_mv`/`float_mv` 推导（close×totalShare / close×liqaShare，股本取自最新财务）
- [ ] `pe_static` 是否保留（无直接源；前端主要用 pe_ttm，可评估停采）
- [ ] 营收增速推导（两期 MBRevenue 之差 / 前值 - 1）
- [ ] 北交所覆盖全量核对
- [ ] 2 年回填并行方案（进程池/分批），重试与断点续传
- [ ] BaoStock 协议端口（8001+）在部署环境的防火墙放行确认（生产容器内已实测可达 ✅）
