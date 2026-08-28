# Steady — 个人 A 股量化研究与模拟交易平台

从"拉数据"→"算策略"→"模拟交易"→"看收益"的完整闭环，可部署到家庭服务器长期自动运行。

> ⚠️ **模拟盘**：本系统不连接任何券商、没有真实资金进出。所有买入/卖出/持仓/净值都是数据库里的模拟撮合，目的是验证策略是否有效；等策略在模拟盘跑稳、回测可信后再接券商实盘（Phase 3，仅预留方向）。

## 设计原则

1. 数据可靠 > 策略复杂
2. 可解释策略 > 黑盒模型
3. 模拟验证 > 实盘尝试
4. 长期复利 > 短期暴利

## 架构

五个服务，单机 docker-compose 部署（不上 k8s）。服务之间不互相调 HTTP，**PostgreSQL 是唯一通信中枢**；只有 nginx 暴露 `:80`，其余容器全绑定 `127.0.0.1`（API 无鉴权，靠"内网 + 单入口"兜底）。

```
  A股数据源 (BaoStock 主源 + AkShare 兜底)
        │
        ▼
┌──────────────┐   写     ┌─────────────────────────┐
│  collector   │─────────▶│      PostgreSQL          │
│  采集+清洗+回填 │         │ 行情/财务/估值/因子/信号   │
└──────────────┘          │ 账户/持仓/委托/成交/净值   │
        ▲                 └─────────┬────────────────┘
        │                           │ 读
        │                   ┌───────▼────────┐   ┌──────────┐
        │                   │ quant-engine   │──▶│ 飞书机器人 │
        │                   │ 因子/信号/回测/早报│   └──────────┘
        │                   └───────┬────────┘
        │                           │ 读信号
        │                   ┌───────▼────────┐   ┌──────────┐
        │                   │    backend     │──▶│ 云端 LLM  │
        │                   │ 模拟交易+API+调度 │   └──────────┘
        │                   └───────┬────────┘
        │                           │ /api/v1
        │                   ┌───────▼────────┐
        └───────────────────│ nginx :80 → 前端 │
                            └────────────────┘
```

| 服务 | 语言 | 职责 |
|---|---|---|
| `collector` | Python | 采集行情/财务/估值/日历（BaoStock 主源 + AkShare 兜底） |
| `quant-engine` | Python | 因子 → 信号 → 回测 → 早报 → 飞书通知 |
| `backend` | Go | REST API + 模拟交易（下单/持仓/净值/对账）+ LLM + 每日调度 |
| `frontend` | React 18 + TS + Vite + ECharts | 深色终端风 Dashboard（已接入真实 API） |
| `postgres` / `nginx` | — | 数据库 / 唯一对外入口（`:80`） |

系统对外只做三类真实调用：数据源拉行情、飞书 webhook 推通知、云端 LLM API 做简报解读。

## 目录结构

```
├── backend/          # Go 后端（API + 模拟交易 + 每日调度，单进程）
├── collector/        # Python 数据采集（BaoStock 主源 + AkShare 兜底）
├── quant-engine/     # Python 量化引擎（因子/信号/回测/通知）
├── frontend/         # 前端 v2（React + TS + Vite + ECharts）
├── deploy/           # Docker Compose、DDL、Nginx 配置、发布产物（release/）
├── docs/             # 设计文档（phase1/phase2 阶段归档 + 活文档）
└── scripts/          # 开发/运维脚本（dev-up / build-release / install 等）
```

## 快速开始

### Docker 一键启动

```bash
# 1. 配置数据库凭据（.env 已被 gitignore，永不入库）
cp deploy/.env.example deploy/.env
vim deploy/.env                      # 填强密码：openssl rand -hex 24

# 2. 构建并启动全部服务
cd deploy && docker compose up -d --build
```

- 前端: http://localhost/
- API: http://localhost/api/v1/stocks（健康检查: `/api/v1/health`）

首次启动时 postgres 会**自动执行** `deploy/postgres/init.sql`（挂载于 `docker-entrypoint-initdb.d`），无需手动初始化。

### 开发模式（host 直跑 + Docker 仅保留 postgres）

```bash
# 1. deploy/.env 的 DB_PASSWORD 需与 scripts/dev.env 一致（默认 quant_pass_2026），供 host 进程连库
docker compose -f deploy/docker-compose.yml up -d postgres   # 只起数据库

# 2. 一键启动：backend + collector + quant-engine + 前端(vite dev)
./scripts/dev-up.sh
#    （--no-pipeline 可跳过采集/引擎调度，只跑 backend + 前端）

# 3. 收尾：停 host 进程，恢复 Docker 代码容器
./scripts/dev-down.sh
```

- 前端（vite dev，HMR）: http://localhost:5173 （`/api` 代理到 8080）
- 后端 API: http://127.0.0.1:8080/api/v1/health

生产部署（**三件套发布模型**：`install.sh` + 配置包 + 镜像包，VM 只运行不编译）见 [deploy/README.md](deploy/README.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/系统手册.md](docs/系统手册.md) | **详细说明书**：14 页前端逐个怎么用 + 四层代码架构 + 数据流/定时任务/部署/配置 |
| [docs/项目详解.md](docs/项目详解.md) | 写给项目主人的完整说明：自动买入原理、因子打分、每日数据流 |
| [docs/进度总表.md](docs/进度总表.md) | 全项目唯一的阶段索引（各阶段归档 + 设计定稿清单） |
| [docs/优化路线图.md](docs/优化路线图.md) | 迭代路线图与待办 |
| [deploy/README.md](deploy/README.md) | 部署运维手册（分支/发布/升级/回滚/备份/安全基线） |

## 开发路线

当前进度详见 [docs/进度总表.md](docs/进度总表.md)。已完成并归档：搭框架、部署上线、数据源稳定化（BaoStock 主源迁移，阶段 1~3 去 Tushare）、飞书通知、可靠性与对账、LLM 集成、回测可信度校准、策略与风控、因子研究闭环。当前处于**第二阶段收尾**：frontend-v2 已接入真实 API（14 页），G1~G8 缺口已补齐，G6 外盘层与策略振荡修复等按路线图推进中。
