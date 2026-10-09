# 数据接入层 datahub — Phase 1 实施蓝图

> **状态**：✅ 已实现（2026-10-09）｜**实现仓库**：[glacierzzz26/datahub](https://github.com/glacierzzz26/datahub)（PR #1 骨架 / #2 收尾）｜**上位文档**：[`数据接入层-datahub.md`](数据接入层-datahub.md)（架构总设计）｜**类型**：Phase 1 可落地蓝图
> **一句话**：把 Phase 1 做到「**一个独立服务，对外经 MCP + HTTP 提供热点/行业数据（实时抓 + 缓存），带 token 鉴权**」，**steady 零改动、无自有 DB**（落库推迟到 Phase 2）。

---

## 0. Phase 1 目标 / 非目标

**目标**
- 建**独立仓库** `datahub`（自包含、独立 CI/版本）。
- 实现采集框架（provider 注册表 + 源链 + 限流/缓存/降级）。
- **只接「外部增强」数据集**：热点 / 行业（steady 现在本就散在 collector 直调、无统一入口，风险最低）。
- 对外 **MCP（Streamable HTTP）+ 原生 HTTP API** 双出口 + **token 鉴权** + `/healthz`。

**非目标（推迟）**
- 不接核心行情/估值/财务/日历采集（**Phase 2**）。
- **不建自有 DB**（Phase 1 热点/行业按需抓 + 缓存即可；落库在 Phase 2 接管核心采集时才需要）。
- 不改 steady（本阶段 steady 的热点仍由 collector 直采；两条线并行、互不依赖）。
- 不含计算产物（因子/信号/绩效）。

> ✅ **已决（2026-10-08）**：Phase 1 **按需取当前值、不建自有 DB**——热点/行业实时抓 + 缓存即可；**建库推迟到 Phase 2**（接管核心采集时）。

---

## 1. 新仓库骨架（独立仓库 `datahub`）

```
datahub/                              # 独立 git 仓库
├── pyproject.toml / requirements.txt  # Python 3.12
├── Dockerfile                         # FROM python:3.12-slim
├── README.md                          # 服务级：端口/出口/鉴权/数据集清单/本地起法
├── .env.example                       # DATAHUB_* 变量
├── docker-compose.yml                 # 单服务（本阶段）
├── .github/workflows/ci.yml           # 独立 CI（lint + pytest + docker build）
├── app/
│   ├── __init__.py
│   ├── config.py                      # env 读取（照抄 collector/app/config.py 风格）
│   ├── auth.py                        # Bearer token 依赖
│   ├── server.py                      # 装配 FastAPI + 挂载 FastMCP + /healthz（入口，装 net 补丁）
│   ├── datasets/
│   │   ├── spec.py                    # DatasetSpec / ParamSpec / ColumnSpec
│   │   ├── registry.py                # DATASET_REGISTRY: dict[str, DatasetSpec]
│   │   └── external.py                # Phase 1 数据集（热点/行业）
│   ├── providers/
│   │   ├── base.py                    # Provider 协议 + with_timeout + 重试
│   │   ├── net.py                     # install_http_timeouts（搬自 collector/app/sources/net.py）
│   │   ├── registry.py                # PROVIDERS + dataset_source_chain()
│   │   └── ext/
│   │       ├── akshare_hotspot.py     # 搬自 collector/app/collectors/hotspot.py 取数函数
│   │       └── akshare_industry.py    # 行业目录/成分（目录首源同花顺；成分仅东财，见 §13）
│   ├── cache.py                       # TTL + 单飞(single-flight)
│   ├── ratelimit.py                   # 信号量 + 最小间隔 + 黑名单冷却
│   ├── http_api.py                    # /v1/... 路由（真实契约）
│   └── mcp_facade.py                  # FastMCP 实例 + 由 DATASET_REGISTRY 生成 tools
└── tests/
    ├── conftest.py                    # 守卫风格（仿 collector/tests/conftest.py）
    ├── test_datasets_contract.py      # DatasetSpec 形状/列一致性
    ├── test_auth.py                   # 401/200
    ├── test_providers.py              # mock akshare，源链降级/冷却
    ├── test_cache.py                  # TTL + 单飞
    └── test_mcp_tools.py              # tool 清单与返回形状
```

---

## 2. 技术选型

| 用途 | 选型 | 理由 |
|---|---|---|
| 语言 | **Python 3.12** | 对齐现 collector/quant-engine，MCP SDK 要求 ≥3.10 |
| HTTP 契约出口 | **FastAPI + uvicorn** | 真实契约 + 自动 OpenAPI；pydantic 校验 |
| MCP 门面 | **mcp（FastMCP，Streamable HTTP）** | 内置 streamable-http；**不用 SSE**（已弃用、自定义头难传） |
| 外部取数 | **requests**（+ `install_http_timeouts` 补丁） | 搬 collector 范式，超时纪律一致 |
| 校验/契约 | **pydantic v2** | 与仓库一致 |
| 测试 | **pytest** | 与仓库一致 |

> **依赖版本**（`fastapi` / `uvicorn` / `mcp`）在实现期**锁具体版本**并用容器内 `python -c "import mcp; print(mcp.__version__)"` 验证——勿凭记忆写死。

---

## 3. 数据集契约代码化（§2 契约目录的代码形态）

```python
# app/datasets/spec.py
@dataclass(frozen=True)
class ParamSpec:
    name: str; type: Literal["str","int","date","code","enum","bool"]
    required: bool = False; default: object = None; enum: tuple = (); desc: str = ""

@dataclass(frozen=True)
class ColumnSpec:
    name: str; type: str; unit: str = ""; desc: str = ""

@dataclass(frozen=True)
class DatasetSpec:
    id: str; title: str
    kind: Literal["external", "raw"]        # Phase 1 全为 external
    params: tuple[ParamSpec, ...]
    columns: tuple[ColumnSpec, ...]
    ttl_seconds: int | None                 # None=不缓存
    paginated: bool = False
    source: str = ""                        # 源链键
```

**`DATASET_REGISTRY` 由 `spec` 机械推导**出：HTTP 的 query 参数与响应列、MCP tool 的 inputSchema 与返回字段、契约测试断言。**出口层零业务逻辑**——这是"MCP 薄门面"的落地保证。

### Phase 1 数据集清单（**已冻结** 2026-10-09，照 provider 实际输出逐字核对）

| id | params | 列（冻结） | ttl |
|---|---|---|---|
| `hotspot.indices` | — | name,code,close,change_pct | 300s |
| `hotspot.sectors_gain` | `top` | name,change_pct,leader | 300s |
| `hotspot.sectors_flow` | `top` | name,net_inflow | 300s |
| `hotspot.hot_stocks` | `top` | rank,code,name,change_pct,board_days,industry | 300s |
| `industry.catalog` | — | name,code,change_pct | 86400s |
| `industry.members` | `industry`(必) | code,name | 86400s |

> 列名/单位**已照 `providers/ext/akshare_hotspot.py` 与 `akshare_industry.py` 的实际输出逐字冻结**（映射时核对 `_pick` 的列名容错），不凭记忆。契约 v1 自此只增不改。
> **空结果即失败**：数据集整体取空（全源失败）→ provider 抛错，服务层转 stale（有旧值）或 503，**绝不静默返回空数组**（§5 / §11）。

---

## 4. Provider 抽象与源链（复用 collector）

- **Provider 协议**（`providers/base.py`）：`fetch(dataset, params) -> list[dict]`，带 `with_timeout`（搬 `collector/app/collectors/base.py:34`）与重试（仿 `run` 3×5s）。
- **注册表**（`providers/registry.py`）：`PROVIDERS: dict[str, Provider]` + `dataset_source_chain(id)`——**骨架照 `collector/app/collectors/daily.py:237 _PROFILES` + `:243` 分发**。
- **取数函数**：`providers/ext/akshare_hotspot.py` **move（非 copy）** 自 `collector/app/collectors/hotspot.py` 的纯取数函数：
  `_fetch_indices` / `_cn_indices_from_em` / `_cn_indices_from_sina` / `_fetch_ths_sectors` / `_sectors_gain_from` / `_sectors_flow_from` / `_fetch_hot_rank` / `_fetch_zt_pool` + 助手 `_pick/_f/_fmt_*`。
  （steady 侧对应代码在 Phase 3 才删；Phase 1 是"复制过来先跑通、Phase 3 收尾时再删本地"——**允许暂时重复，但登记为待删**。）
- **闸门**：`ext_enabled(dataset)`——`DATAHUB_EXT_ENABLED`（总开关）× `DATAHUB_EXT_DATASETS`（数据集白名单），**默认空 = 全关**（照 `collector/app/config.py:95 baostock_enabled` 的双闸门范式）。未翻闸 → 该数据集返回 503，不发外部请求。

---

## 5. 能力层

- **缓存**（`cache.py`）：进程内 `dict[key] = (expire_at, payload)`，`key = f"{id}:{canonical(params)}"`，TTL 取 `DatasetSpec.ttl_seconds`。
- **单飞**（single-flight）：每 key 一把锁，并发只放一个请求到上游，其余等结果——**防并发消费打爆上游**的核心。
- **限流**（`ratelimit.py`）：每 provider 一个 `Semaphore(N)` + 最小间隔（照 `TENCENT_RATE_LIMIT` 语义）。
- **黑名单冷却**：`is_source_blocked(exc)`（搬 `sources/tencent.py:61`/`baostock.py:67` 语义）命中 403/429/封禁 → 冷却期内不发请求。
- **降级**：源链左→右；全失败时——有缓存旧值 → 返 **stale + `meta.stale=true`**；无 → 结构化错误 `{code:503,...}`。**绝不静默返回空**。

---

## 6. 鉴权

- `auth.py`：`require_token` 依赖，校验 `Authorization: Bearer <DATAHUB_TOKEN>`；缺失/错误 → **401**。
- **`/healthz` 免鉴权**（探针用）。
- `.env` `chmod 600`；token 属密钥，不进仓库、不进日志明文。

---

## 7. 双出口

- **原生 HTTP**（`http_api.py`，真实契约）：
  - `GET /v1/healthz`（免鉴权）
  - `GET /v1/datasets`（元数据清单：id/title/params/columns/单位/ttl）
  - `GET /v1/datasets/{id}`（query 传参；信封 `{code,message,data,meta}`）
- **MCP 门面**（`mcp_facade.py`）：每个 `DatasetSpec` → 一个 tool（`hotspot_indices` 等）+ `list_datasets`；`mcp.run(transport="streamable-http", stateless_http=True)`。
- **共享内核**：两出口都调 `get_dataset(id, params)`——HTTP 与 MCP **零逻辑重复**。

---

## 8. 配置与 `.env`

```
DATAHUB_PORT=8100
DATAHUB_TOKEN=change_me_strong_token          # openssl rand -hex 32
DATAHUB_LOG_LEVEL=info
# 外部源闸门（默认全关 → 部署零外部请求）
DATAHUB_EXT_ENABLED=                          # 1/true 打开
DATAHUB_EXT_DATASETS=                         # 逗号白名单，如 hotspot.indices,industry.catalog
# HTTP 超时（沿用 collector 口径）
DATAHUB_HTTP_CONNECT_TIMEOUT=5
DATAHUB_HTTP_READ_TIMEOUT=15
DATAHUB_CACHE_TTL_DEFAULT=300
# 限流
DATAHUB_RATE_LIMIT=0.2
```
> Phase 1 **无 DB 变量**（落库推迟 Phase 2）。

---

## 9. 部署（独立仓库）

- `Dockerfile`：`python:3.12-slim`，`pip install -r requirements.txt`，入口 `python -m app.server`（在入口装 `install_http_timeouts`）。
- `docker-compose.yml`（单服务）：绑 `127.0.0.1:8100`（拓扑未定时先内网；若需跨机，改绑 LAN IP + 走反代/直连 + token）。healthcheck 用 `python -c urlopen('http://127.0.0.1:8100/v1/healthz')`（slim 无 curl 范式）。
- **独立 CI**：lint + `pytest` + `docker build`；**独立版本**（tag）。
- **与 steady 的关系**：本阶段零耦合（steady 不改）。拓扑待定（D3）——Phase 1 先在本机/同宿主跑通，部署位置后续再定。

---

## 10. 测试

| 测试 | 断言 |
|---|---|
| `test_datasets_contract` | 每个 `DatasetSpec` 参数/列定义自洽；列名与 provider 实际输出一致 |
| `test_auth` | 无 token → 401；错 token → 401；对 token → 200 |
| `test_providers` | mock akshare：源链左→右降级、黑名单冷却命中即停、来源缺失列不中断 |
| `test_cache` | TTL 过期重取；并发同 key 只打一次上游（单飞） |
| `test_mcp_tools` | tool 清单 = 数据集数；`inputSchema` 由 params 生成；返回形状正确 |

---

## 11. 验收清单（Phase 1 done 判定）

- [x] 独立仓库建立、CI 绿、可独立构建镜像与启动。（repo 公开；CI `lint-test`+`docker-build` 均绿）
- [x] `curl -H "Authorization: Bearer $T" /v1/datasets` 列全；无 token → 401。（`test_auth` 7 例）
- [x] MCP 客户端 `tools/list` 列全；tool `inputSchema` 由 params 生成。（`test_mcp_tools`）真实热点数据取数受**双闸门**控制，翻闸后验证（本环境 `hotspot.indices` 源实测可通）。
- [x] 未翻闸（`DATAHUB_EXT_ENABLED` 空）时**零外部请求**、返回 503（部署零行为变更）。（`test_gate_off_no_external_call` 断言 `called==0`）
- [x] 上游全失败 → 返回 stale/结构化错误，**不返回空数组**。（`test_stale_fallback_when_upstream_fails` + `test_hot_stocks_all_sources_fail_raises`）
- [x] steady **未改动**（本阶段解耦）。
- [x] 工程门禁：`ruff check .` 干净、`pytest` **42 passed**。

---

## 12. 接缝与后续

- **实现登记**：Phase 1 代码在独立仓库 [glacierzzz26/datahub](https://github.com/glacierzzz26/datahub)（PR #1 骨架 → `dev`；PR #2 收尾）。默认分支 `dev`，`main` 已加保护规则（禁直推，PR 必过 `lint-test`+`docker-build`）。
- **契约 v1 冻结**：本阶段固化的 dataset 契约即 v1（见 §3）；Phase 2/3 只增不改（改列必升版本）。
- **Phase 2**：datahub 建自有库 + 接管核心采集（行情/估值/财务/日历），采集器 move 进来（含 `guard_factor` 等校验），逐数据集对账切换。
- **Phase 3**：steady 侧 `data_source.py` / backend 消费层改按需调 datahub；collector 退役；**删除 §4 里"暂复制"的本地取数函数**。
- **待删登记**：Phase 1 从 collector 复制来的取数函数，必须在 Phase 3 收尾时从 steady 删除（避免长期双份）。

---

## 13. 未决 / 风险

1. ~~**MCP SDK 版本**~~ → **已决**：锁 `mcp==1.30.0`（`<2`；mcp 2.x 把 `FastMCP` 改名 `MCPServer`），容器内 `import mcp` 已验证。
2. ~~**行业成分 `industry.members` 首源未定**~~ → **已决**：`industry.catalog` 首源取**同花顺**（`stock_board_industry_name_ths` + `summary_ths` 按名合并涨跌幅），东财兜底；`industry.members` akshare **仅有东财** `stock_board_industry_cons_em` 一个成分接口，无同花顺替代——**东财不可达时返回 503（实现受限，非代码缺陷）**，翻闸前须确认该源在本环境可达。
3. **持久化**：Phase 1 **已决无 DB**（按需取当前值）；热点/行业历史留痕随 Phase 2 建库一并解决。
4. **上游限流**：Phase 1 与 collector 的 `hotspot` cron（08:45）**并行抓同一批源**——用 TTL≈300s + 信号量错峰，翻闸前观察上游是否报 429/封禁。
5. **拓扑**（D3）：跨机部署时的鉴权/网络面（LAN 明文 token）。
6. **东财 board 接口在目标环境稳定不可达**（实测 `RemoteDisconnected`）——已按 §13.2 调整首源；`hotspot.indices` 走东财→新浪降级，`sectors_*` 走同花顺。
