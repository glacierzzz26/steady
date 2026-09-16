# 数据源评估：腾讯行情源接入（Tencent / gtimg）

> **状态**：📋 定稿（2026-09-16）｜**落地 issue**：[#15](https://github.com/glacierzzz26/steady/issues/15)（本文为实现蓝本）｜**上游评估**：[#13](https://github.com/glacierzzz26/steady/issues/13)
>
> 本文沿用 [`数据源评估-BaoStock.md`](数据源评估-BaoStock.md) 的体例：现状 → 实测对照 → 边界结论 → 风险 → 实施路径。所有实测数据均为 **2026-09-16 从生产机（`ssh quant@192.168.0.201`）现场验证**，非文档推测。

---

## 1. 现状：采集链已名存实亡

生产日志（48h）逐源统计：

| 源 | 实测结果 | 判读 |
|---|---|---|
| 东财 `push2.eastmoney.com` / `push2his` | **HTTP 000（连接根本不成立）** | 主源已死 |
| 新浪 `hq.sinajs.cn` | 裸请求 403，带 `Referer: https://finance.sina.com.cn` → 200 正常 | **实际唯一活着的腿** |
| BaoStock | 现场 `10002007 网络接收错误`，query 返回 0 行 | 当前不可用 |
| 腾讯 `qt.gtimg.cn` / `proxy.finance.qq.com` | **200 ✅** | 可用，快 |

日志佐证：48h 内 **3038 次 `RemoteDisconnected`、3059 次降级新浪**。

**痛点量化**：逐只串行 + `RATE_LIMIT_SECONDS=3` 固定 sleep → 实测 **4.07 s/只**。800 池日同步 25–40 分钟（已顶到 19:00 因子截止窗口），全市场 5550 只线性外推 **6–7 小时** → 当日出信号不成立。

---

## 2. 腾讯端点契约（实测）

### 2.1 日K线

```
GET https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get
    ?param=<symbol>,day,<start>,<end>,<count>,<fq>
```

- `symbol`：`sh600519` / `sz000001` / `bj430047`
- `fq`：``（不复权）/ `qfq`（前复权）/ `hfq`（后复权）
- 响应：`data.<symbol>.<key>`，`key` = `day` / `qfqday` / `hfqday`
- **行结构（下标，勿记错）**：

  ```
  [0]=date  [1]=open  [2]=CLOSE  [3]=HIGH  [4]=LOW
  [5]=volume  [6]={}  [7]=turnover_pct  [8]=amount(万元)
  ```

  ⚠️ **`close` 在下标 2，不是 4**（`date,open,close,high,low` 序）。这是最容易踩的坑。

- `count` 上限约 2000；**日期窗口**（`start`+`end`）可正常分页。
- 实测延迟 **0.15 s/次**。

### 2.2 批量快照

```
GET https://qt.gtimg.cn/q=sh600519,sz000001,...     # 50+ 只/次
```

- 响应为 **GBK 编码的 JS 行**（非 JSON）：`v_<symbol>="..."`，`~` 分隔。
- 关键下标：`[3]`=现价 `[4]`=昨收 `[5]`=开盘 `[6]`=成交量(手) `[30]`=时间戳 `[33]`=最高 `[34]`=最低 **`[37]`=成交额(万元)**。
- 实测 58 只请求 / 48 只返回——**部分代码会缺，调用方须容忍缺失**，不可把部分成功当整体成功。
- ⚠️ **快照成交量单位按板块不同**（同 §2.3）：主板/创业板 = 手，科创板 688/689 = 股（÷100）。实测 688111 快照 `[6]=4482345` 与日K ÷100 后一致。

### 2.3 单位（实测定表）

| 板块 | 代码前缀 | 成交量单位 | 处理 |
|---|---|---|---|
| 主板/创业板 | `600/601/603/000/002/300` | **手** | ×1（7 只实测与库逐位一致） |
| 科创板 | `688/689` | **股** | **÷100**（688111/688981/688036 三只比值恰为 100；快照同样） |
| 北交所 | `43/83/87/92` | **未实证** | 暂按手，由 `audit-tencent` 定案 |
| 成交额 | — | **万元** | **×10000** → 元 |

行长度 < 9 视为畸形，丢弃 + warning，不崩。**日K `count` 上限 2000**，且 `start` 参数被忽略（`count` 为「截至 end 的最近 N 行」）→ 翻页须把 `end` 前移。

**2026-09-16 实现期另实证两点**：① 日K 与快照两路成交量在 600519/300750/688111/920001 上**逐位一致**；② 快照 `[4]` 昨收（600519=1272.75）= 日K 09-15 收盘 → 除权探测基准成立。


---

## 3. 边界结论

### 3.1 ⛔ 腾讯 `hfq` 无用于派生复权因子（硬结论）

实测反例（600519 茅台，60 日窗口）：

| 日期 | raw close | 腾讯 hfq | hfq/raw |
|---|---|---|---|
| 2026-09-10 | 1285.13 | 9035.73 | **7.03099** |
| 2026-09-11 | 1275.16 | 8979.62 | **7.04196** |
| 2026-09-14 | 1277.96 | 8995.38 | **7.03886** |
| 2026-09-15 | 1272.75 | 8966.06 | **7.04464** |
| 2026-09-16 | 1258.00 | 8883.05 | **7.06125** |

- 比值**单调漂移** 7.006 → 7.061（60 日），非恒定 → 不是合法的等比复权序列。
- 无除权日的日收益也偏差 **0.09–0.34%/日**（`hfq` 日收益 ≠ raw 日收益）。
- 同一日内 `hfq_open/raw_open` ≠ `hfq_close/raw_close`（7.0096 vs 7.0247）。

**对照**：新浪 `hfq`/`raw` **恒为 8.8825**，与库内 `daily_price.adj_factor` 完全一致。

→ **`adj_factor` 只能继续走新浪腿；腾讯 hfq 永久禁用。** 代码中以 `tencent.daily_pairs()` 显式抛异常 + 模块 docstring 双重封死，防后人"优化"踩坑。

### 3.2 ✅ 腾讯不复权 OHLCV 与库内逐位一致

抽样 600519 八年（不复权 close）：`2018-06-25=765.56`、`2020-06-01=1419.50`、`2021-06-01=2240.95`、`2023-06-01=1635.92`、`2024-06-03=1639.39` —— 与 `daily_price` **完全一致**。

→ 腾讯适合做 **原始 OHLCV 主源**，不适合做因子源。

### 3.3 目标架构

```
原始 OHLCV  ← 腾讯（快 20×、支持北交所）
复权因子    ← 新浪 hfq（恒等比，= 库内口径）→ 过 guard_factor
兜底        ← BaoStock（派生因子，过守卫）
批量快照    ← 腾讯 qt.gtimg.cn（全市场 ~17 秒）
```

与既有 BaoStock 混合方案（"OHLCV 走 BaoStock + 因子留 Tushare"）**完全同构**——已在本仓库验证过的模式，零口径漂移。

---

## 4. 源优先级（方案罗盘）

按 scope 显式定义，env 可覆盖，左→右为降级方向。

| Scope | 1 主 | 2 降级 | 3 兜底 | 变更 |
|---|---|---|---|---|
| **日线原始 OHLCV** | **腾讯** | 新浪 | BaoStock | 🆕 本次 |
| **复权因子 adj_factor** | **新浪 hfq** | BaoStock 派生 | ⛔ 腾讯禁用 | 🆕 本次（显式化） |
| **当日快照（批量）** | **腾讯** `qt.gtimg.cn` | 逐只走日线链 | — | 🆕 本次 |
| 估值 valuation | BaoStock → AkShare | — | — | 不动 |
| 财务 finance | AkShare 全市场快照 | — | — | 不动 |
| 指数 index | AkShare → BaoStock | — | — | 不动 |
| 交易日历 calendar | BaoStock → AkShare | — | — | 不动 |
| 热点/外盘 hotspot | 纯 AkShare | — | — | 不动 |

**关键设计：源链以「整对」`(raw_df, hfq_df)` 为单位，而非单腿。** 每个 profile 返回完整的两份 DataFrame，`build_rows()` / `guard_factor` / `cross_check_splits` **一行不改**。`tencent` profile 在内部把腾讯 raw 与新浪 hfq 拼成一对——上层完全无感。

**双闸门、默认关**（部署代码本身不改生产）：

| 变量 | 作用 | 默认 |
|---|---|---|
| `TENCENT_ENABLED` | 保险丝（镜像 `baostock_enabled`） | 关 |
| `TENCENT_SOURCES` | 参与哪些 scope（如 `daily`） | 空 |
| `DAILY_SOURCE_CHAIN` | **顺序**（profile 名，左→右） | `akshare`（= 现状） |

两者都不点名腾讯 → 一条腾讯请求都不发。

---

## 5. 实施路径

### 阶段 1：适配层 + 对账（不碰生产路径）

- 新增 `collector/app/sources/tencent.py`，镜像 `sources/baostock.py` 的「同形状输出」：
  - `tx_code()` 代码前缀、`daily_raw()`（日期窗口翻页，输出与 `baostock.daily_pairs` 的 raw **同名中文列**）、`quote_batch()` / `snapshot_rows()`、`is_source_blocked()`；
  - `daily_pairs()` = **显式 tripwire**（抛"非恒定比例，禁用"）；
  - 单位归一（科创板 ÷100、万元 ×10000）在 `daily_raw` 内完成。
- 新增只读 CLI `audit-tencent`：逐 (code, date) 比对腾讯 vs `daily_price` 的 `close/volume/amount`，打印**分板中位比值**——据此定科创板除数与**北交所单位**（当前唯一未实证项）。

### 阶段 2：接入源链

- `config.py` 加 `TENCENT_*` + `DAILY_SOURCE_CHAIN` + `tencent_enabled()` / `daily_source_chain()`。
- `daily.py`：新增 `fetch_pair_tencent()`（腾讯 raw + 新浪 hfq 混合腿）+ `_align_hfq_to_raw()`（日期集对齐）+ 按**名字**分发的 profile 链（`globals()` 查表，**勿在 import 期快照函数对象**——会破坏 `test_daily.py` 的 monkeypatch）。
- **新增不变量**：raw 非空但因子腿为空 → **必须 raise**，绝不落 NULL `adj_factor`（`upsert` 的 `update_cols` 含该列，NULL 会覆盖库内好因子——与 08-28 index amount 清空事故同源）。
- `remediation.py` 自愈分流也认腾讯 `is_source_blocked`（防封禁期逐股锤，08-28 教训）。

### 阶段 3：灰度翻转（生产，人工审核后）

1. 部署不带 env → 链 = `akshare`，**零变化**；
2. 设 `TENCENT_ENABLED=1 TENCENT_SOURCES=daily` 但仍不写 `DAILY_SOURCE_CHAIN` → **仍零变化**（验证保险丝独立生效）；
3. `DAILY_SOURCE_CHAIN=tencent,akshare` 单只 canary → 核对 `adj_factor` 逐位一致；
4. 全池 → 观察 `guard_factor` 拒收率 / `后复权缺失` / `单位疑似错配`；
5. 回填重复同序（dry-run → 小批 `--codes` → 全池）。

---

## 6. 风险

1. **非官方抓取端点**（同东财/新浪性质）：无 SLA、限频策略不公开、ToS 需自行判断。按"限频 + 多源互备"姿态接，**不当稳定 API 依赖**；`TENCENT_RATE_LIMIT`（默认 0.2s）+ 冷却镜像 08-28 BaoStock 封禁教训。
2. **北交所单位未实证**（阶段 1 的 `audit-tencent` 定案）。
3. **同日新鲜度**：实测 09-16 盘中 `day` 已含当日行，风险低；灰度时确认 18:10 窗口可取到当日（若滞后则当日腿留在新浪）。
4. **腾讯 hfq 永久不可用**——由 tripwire + docstring 双重封死。
5. **快照端点会缺码**（58 请求 / 48 返回）：调用方须容忍缺失，不可当整体成功。
6. **因子列 NULL 覆盖**——见 §5 阶段 2 不变量。

---

## 7. 与既有文档的关系

- 本文是 [`数据源评估-BaoStock.md`](数据源评估-BaoStock.md) 的**续篇**：BaoStock 定的是"A 股基本面 + 日线核心"，本文补的是"当 BaoStock 也不可用时的日线主源"。
- BaoStock 的边界结论（§3.1 复权语义坑、51% 阶跃）在本文继续成立——**因子口径不变**，只是原始行情换了更快的源。
