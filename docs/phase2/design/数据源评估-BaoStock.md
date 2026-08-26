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

### 2.1 pe_ttm 口径对账（与 Tushare 逐位核对，2026-08-26）

`pe_ttm` 不是原始字段，是各家独立派生的比值（`收盘价 × 股本 / TTM净利`），口径差异**与数据源质量无关**。对账结论（000333 美的 / 600000 浦发，两股全季度财报 + 日线时间序列实测）：

| 事实 | 数值 |
|---|---|
| Tushare `daily_basic.pe_ttm`（现库取值） | 美的 14.8814 / 浦发 6.1008 |
| BaoStock 日线 `peTTM`（同 8/26） | 美的 14.9114 / 浦发 5.9875 |
| 浦发 Tushare 值 = `close / epsTTM(26Q1)` | 9.21 / 1.509645 = 6.1008 **精确相等** |
| 浦发 BaoStock 日线 `close/peTTM` 时间序列 | 7/1–8/12 恒 1.5096（=26Q1 口径）；**8/13 半年报发布当天跳变 1.5382 后恒定**（=H1-2026 口径） |
| 美的 `close/peTTM` | 恒 ~5.805（略低于 epsTTM(26Q1)=5.812985，**股本口径差 0.13%**），7/16 后缓降 0.2%（股本漂移） |

**根因**：① 浦发 1.9% 的大偏差 = **财报更新时点差**——BaoStock 在半年报发布日（8/13）当天切到 H1-2026 的 TTM 口径，Tushare 到 8/26 还停在 26Q1 口径（两者在"同一财报口径"下完全一致）。② 美的 0.2% 偏差 = **股本口径差**（期末股本 vs 供应商每日维护股本）。与净利口径（归母/合并）无关——浦发是银行无少数股东问题。

**对系统影响**：数据质量检查的 `valuation` 项**只查新鲜度 lag，不比对 pe 数值**（`data_quality.py` `_check_valuation`），故供应商间 pe 差不影响健康检查。迁移到 BaoStock 后 pe_ttm 会在每季财报日出现 ≤2% 跳变——这其实是**更「新鲜」**（Tushare 反而滞后数日~数周），但因子侧（`factor_service`/`factor_research` 读 pe_ttm）需知悉该跳变。

### 2.2 adj_factor 复权因子对账（全历史逐位核对，2026-08-26）

用新适配层（`adjustflag=1` 后复权，`BS因子 = hfq_close / raw_close`）在**生产 collector 容器内**对 4 只代表股 × 10 个历史日期实测，与库内 Tushare `adj_factor` 比对：

| 股票 | BS因子 / Tushare因子 | 特征 |
|---|---|---|
| 600519 茅台 | **0.86341**（2018–2026 全期恒定至 5 位小数） | 恒为常数倍 |
| 000333 美的 | ≈0.9997；2018→2020 一次 −0.025% 阶跃后恒定 | 几乎相等，1 次小阶跃 |
| 600000 浦发 | ≈0.7686；2024→2026 一次 −0.011% 阶跃后恒定 | 1 次小阶跃 |
| 689009 九号 | 1.00001 | 相等 |

**原始行情全期逐位一致**：close Δ=0.000；vol Δ≤1 手（股÷100 取整）；amount Δ≤0.5 元（元精度）。库内 Tushare 因子值恒为 4 位小数（如 8.8825），故比率内 Δratio≈±1e-5 全是舍入噪声。

> **BaoStock 停牌日特性（对账时确认，无需改代码）**：停牌日 BaoStock 仍返回行，`volume=0` 且收盘价重复上一交易日（美的 2018-10 吸收合并小天鹅停牌、2019-05 等实测）；Tushare 停牌日不返回行。现清洗器 `clean_daily_rows` 已把 `volume<=0` 行丢弃（`cleaners/__init__.py:69`），**入库结果两边一致**（停牌日均无行）——只多日志噪音。

**全池对账（2026-08-26，dev 全量回填 800 只 × 2016-08~2026-08 vs 生产 Tushare，共 169 万行）**：

| 对账项 | 结果 |
|---|---|
| close | ✅ 超差 202/169 万行，全部 ±0.01 元（两位小数舍入，非系统性） |
| volume | ✅ 超差 1 行 |
| amount | ✅ 相对差 >0.01% 仅 6 行（原先 ±0.5 元**绝对**阈值误报 5.9 万行——实为亿元级金额的个位数元舍入；**须用相对容差**） |
| adj_factor | ⚠️ **分段常数倍成立，但 411/799 只存在 >0.05% 阶跃**（4 只代表股样本太小，漏掉了风险尾部） |

**adj_factor 阶跃细分（411 只）**：
- **242 只 = 仅数据尾端（≤7 交易日）**：BaoStock 对最近一次分红的复权调整**滞后**于 Tushare（新分红入库后几天因子未更新）——**边缘暂态、自愈**，只影响最新几行。
- **169 只 = 历史中段真实阶跃**（除权/送转/配股事件边界）：
  - 137 只 0.05–0.5%（供应商对除权处理时点/幅度细微差异）；
  - 5 只 0.5–1.2%（603858/600025/600968/600346/002500）；
  - **2 只 >10%：000001 平安（16.7%）、002466 天齐（15.8%）——已实证为 BaoStock 因子数据缺陷（见下）**。

**两个已实证的 BaoStock 因子缺陷**：
1. **000001 平安银行——虚假调整（BaoStock bug）**：2020-12-31 无任何除权（raw close 19.20→19.34 上涨），BaoStock hfq 却同日下跌 16.2%（2303→1930）——**BaoStock 自身 hfq/raw 内部不一致**，纯属虚假调整；Tushare 因子同日恒定。任何跨该日的复权收益，BaoStock 会凭空差 16.7%。
2. **002466 天齐锂业——配股调整滞后**：2019-12-26 除权日 Tushare 即调整因子，BaoStock 迟至 2020-01-02 才调整，中间 ~5 个交易日复权口径差约 16%（除权日单日复权收益：Tushare +10.4% vs BaoStock −7.5%）；事后残余 ~0.3% 永久偏差。

**复核结论（修正原「≤0.025% 小阶跃」——样本太小）**：
1. **OHLCV（close/vol/amount）= 逐位一致 ✅**——BaoStock 作**日线原始行情主源**成立。
2. **adj_factor 是分段常数倍，但 51% 的股票存在 >0.05% 阶跃，其中 2 只为数据缺陷**。**BaoStock 复权因子不能作为唯一来源**。
3. 系统内因子消费者均为比值口径（`factor_service` 前复权 = `close × factor / 窗口最新 factor`；锚点因子在比值中抵消）：常数倍段内逐位等价；**跨阶跃边界的复权收益差异 = 阶跃幅度**——多数 <0.5% 可忽略，但平安/天齐跨边界差 15%+，不可忽略。

**阶段 1 对账放行标准（复核结果）**：
- `close/volume/amount`：✅ **通过**（close ±0.01 元、vol ±1 手、amount **相对差** 0.01%）。
- `adj_factor`：❌ **未通过**。→ 改为**混合方案**：**BaoStock 主供 OHLCV；adj_factor 保留 Tushare**（或引入第三源）。生产翻转 k 归一化的前提不成立——阶跃是**分段**的，不是常数倍，乘 k 只解决锚点不一致、解决不了除权边界分歧（平安 bug、天齐滞后都在分段边界上）。

**生产翻转连续性（阶段 2 前置硬要求）**：**已被混合方案取代**——adj_factor 不切 BaoStock，因子列保持 Tushare 单一来源，无混合锚点问题。原方案（记录备查）：若曾打算新增日直接落 BaoStock 因子，茅台因子列会在翻转日从 8.8825 跳到 7.6693（−15.8%）假跳变，须乘每股常数 k 归一化；但**该方案仅解决常数倍锚点差，解决不了分段阶跃（平安/天齐缺陷在分段边界上），故弃用**。

> 注：与 §4 风险 1 的「方案 A/B」是两回事——那里 A/B 指**采集时如何得到 factor**（本迁移已选 A：存 close + 派生 `hfq/raw`）；此处 A/B 指**生产翻转时如何保持因子列连续**。

## 3. 替换对照（按系统表）

| 系统表/需求 | 现源 | BaoStock 覆盖 | 结论 |
|---|---|---|---|
| `daily_price` | 东财→新浪 | OHLCV/量额 ✅（全池逐位一致）；`adj_factor` 需推导且**全池 51% 有阶跃、2 只已实证缺陷** | ⚠️ OHLCV 可切 BaoStock；**factor 保留 Tushare（混合）** |
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

   ⚠️ **实测确认的 adjustflag 语义坑**（8/26 用 689009 九号公司 + 600519 茅台验证）：
   - `adjustflag=1` = **后复权**：茅台 8/26 close=9991.51（=原始 1302.8 × 累计因子 7.67），**与 Tushare `adj_factor` / 东财 hfq 同口径** → 派生 `adj_factor` 必须用它。
   - `adjustflag=2` = **前复权**：最新日 = 原始价（茅台 1302.8、九号 43.15），用它派生 adj_factor 会在最新日得 1.0，**历史全部错 6.4%**（九号实测）。
   - `adjustflag=3` = 不复权。
   - 适配层必须写 `adjustflag="1"` 取后复权，并在阶段 1 对账里抽查一只高分红股（茅台）比对 Tushare factor。
   - **⚠️ 全池对账后结论（§2.2）**：茅台/美的/浦发等常数倍成立，但**全池 51% 的股票有 >0.05% 阶跃，其中 000001 平安（虚假调整 16.7%）、002466 天齐（配股滞后 15.8%）为已实证的数据缺陷**。→ **adj_factor 不能切 BaoStock（混合方案：OHLCV 用 BaoStock、factor 留 Tushare）**。
2. **字段单位校准**：实测浦发 `liabilityToAsset=0.009182`，与 `1-1/assetToEquity≈0.918` 差 100 倍——负债率值单位/口径需对着真实报表读一次再落库。
3. **BaoStock 自身稳定性**：免费社区服务，高峰偶发慢、数据约 18:00 后更新（**昨日数据次日早盘可用 ✅**，满足 08:45 简报表）。但无限额、无地域封锁，且采集本就是逐股循环（现东财/新浪也是逐股，**调用形状不变**），个人规模（~5000 股 + 每日增量）无压力。
4. **回填规模**：BaoStock 无「按日全市场快照」接口（Tushare `daily` 有），2 年回填 = ~5000 股串行会慢，需**并行/分批**——迁移里唯一要新写的工程点。
5. **北交所覆盖**：BaoStock 支持 bj 代码（bj.43/83/87…），但覆盖列表需回填时用 `query_stock_basic` 全量核对一遍（现系统 stock_basic 含 SH/SZ/BJ）。

## 5. 迁移路径（实施时按此推进）

- **阶段 1（止血 + 对账）✅ 完成（2026-08-26）**：新增 `collector/app/sources/baostock.py` 适配层（**复刻 `sources/tushare.py` 的「同形状输出」模式**——上层 build_rows/save/清洗/重试不动，采集器只换主源、AkShare/Tushare 保留兜底）。切 `daily` + `calendar` 两个采集器，dev 库回填后与现有 Tushare 数据**逐位对账**。
  - **对账结果**：OHLCV 逐位一致 ✅；adj_factor 未通过（51% 阶跃，含 000001 虚假调整 / 002466 配股滞后两个实证缺陷）→ **放行范围收窄为混合方案**。
  - **混合方案已落地**：`daily.py` BaoStock 分支 = 「OHLCV 走 BaoStock + adj_factor 走 Tushare」。**因子按交易日批量拉取**（`tushare.factor_map_by_date()` 1 次调用全市场 + 模块级缓存）——**2026-08-27 生产验证暴露 `adj_factor` 免费档限频苛刻（prod 实测 5次/天+1次/分钟、dev 1次/小时），原逐股 `factor_map()` 在全市场同步第一只就打爆配额、混合路径实际永远降级 AkShare，故重构为批量**（`daily.py` `_fill_factor_cache`，测试锁定缓存复用）。因子缺失时整段降级 Tushare 保连续性（`tests/test_daily.py` 三例混合测试锁定）。**dev 库因子列已用 Tushare 覆盖**（茅台 7.6693→8.8825，平安/天齐缺陷消除），复核：全池因子比值 dev/prod 偏移 0 只 >0.01%。
  - **生产 08-24 因子损坏事件（2026-08-27 发现并修复）**：Tushare 全市场 glitch——08-24 当日无任何真实分红（BaoStock 分红核对），但 **606/799 只**复权因子单向尖刺（74 只 >1%，最高 ±50%），隔日 08-25 全自愈；旧 Tushare snapshot 路径忠实落库。**孤立尖刺规则**修复（f(D) 与两侧邻日均不同才修；真实除权 f(D)==f(D+1) 天然跳过）prod+dev，复核：窗口内 >1% 尖刺归零、dev/prod 因子逐位比对 14,392 行 0 偏差。
  - **生产翻转**：待阶段 2 统一回填 + 灰度。dev 现已 = 混合终态（BaoStock OHLCV + Tushare 因子）。
- **阶段 2**：`valuation`/`finance`/`index`/`stock_basic`(保留 industry 来源) 逐个切换 → 生产回填 + 上线。hotspot/外盘层不动。
- **阶段 3（可选）**：Tushare 降为纯兜底，去掉 token 与 120/min 依赖；G6 两市成交顺带用 BaoStock `sum(amount)` 点亮。

工作量约一个迭代量级；最高风险在复权口径切换，故阶段 1 先做对账再放大。

## 6. 开放问题清单（实施启动时逐项确认）

- [x] 复权口径选 A（派生 adj_factor）还是 B（切后复权价）——**已定：A**（适配层存 close + 派生 `hfq/raw`）；但 **adj_factor 本身不切 BaoStock**（§2.2 全池对账：51% 阶跃、2 只实证缺陷）→ 混合方案。
- [ ] **待决策**：adj_factor 去 Tushare 依赖的路径——① 长期保留 Tushare 供 factor；② 引入第三复权源（如东财 qfq 接口）校验/兜底；③ 若接受除权边界 ≤0.5% 差异，再评估全量切 BaoStock 的收益是否覆盖风险（平安/天齐类缺陷需先能自动识别拒收）。
- [ ] `liabilityToAsset` 单位校准（×100 或改用 1-1/assetToEquity）
- [ ] `total_mv`/`float_mv` 推导（close×totalShare / close×liqaShare，股本取自最新财务）
- [ ] `pe_static` 是否保留（无直接源；前端主要用 pe_ttm，可评估停采）
- [ ] 营收增速推导（两期 MBRevenue 之差 / 前值 - 1）
- [ ] 北交所覆盖全量核对
- [ ] 2 年回填并行方案（进程池/分批），重试与断点续传
- [ ] BaoStock 协议端口（8001+）在部署环境的防火墙放行确认（生产容器内已实测可达 ✅）
