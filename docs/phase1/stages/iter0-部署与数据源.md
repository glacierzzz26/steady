# Iteration 0：部署上线 + 数据源稳定化

## 目标

把本地能跑的系统部署成"每天自动运行"的生产环境，并解决数据源可靠性（免费 AkShare 不稳定、限频）：
1. **生产部署**：单机 docker-compose 稳定化，VM 只运行不编译，一键安装、可版本化回滚。
2. **数据源稳定化**：Tushare 主源 + AkShare 兜底，失败自动降级不中断。

> 实际执行顺序交错：飞书通知（Iteration 1 内容）08-21 凌晨已完成，本迭代 08-21 晚起做部署。首个生产基线 `c8d0651` 同时包含两个迭代的成果。

## 时间

| 起 | 止 | 说明 |
|---|---|---|
| 2026-08-21 22:09 | 2026-08-22 01:25 | 部署稳定化 + 生产基线 + Tushare 接入 |
| 2026-08-22 20:43 ~ 08-23 13:50 | 发布后维护 | 见"遗留"节（install 升级修复、release 目录不入库） |

## 设计

1. **VM 只运行不编译**（`37876e7`）：本地（有 Docker）构建镜像 → 打 `steady-images*.tar.gz` 镜像包 → scp 到 VM → VM 用 run-only compose 直接加载。VM 上不装编译链、不拉镜像仓库。**git-SHA 版本化**：镜像 tag 与目录命名带提交 SHA，可精确回滚。
2. **三件套发布模型**（`5afb615`）：`build-release.sh` 产出 `deploy/release/steady-<日期>-<SHA>/` 三件套 = 镜像包 + compose + `install.sh`。`install.sh` 一键部署且**幂等**：复用既有 postgres 项目名与 .env（数据卷/密码继承），不新建空卷。
3. **配置归位**（`334d1d9`）：业务配置全走 `app_config` 表（前端设置页可改、存库），env 层只留数据库凭据。Tushare token 实现为 `app_config.tushare.token`（原规划读环境变量的方式被取代）。
4. **compose 稳定化**（`30e2e78`）：`restart` 策略 / 内存限额 / 端口收敛（仅 nginx :80 对外）/ 备份压缩。
5. **数据源切换**（`8fb3fa9`）：六类采集全部 Tushare 主源优先、AkShare 兜底——日线 `daily`+`adj_factor`、估值 `daily_basic`、财务 `fina_indicator`、列表 `stock_basic`、日历 `trade_cal`、指数 `index_daily`。每日增量主路径按交易日全市场快照（日线 2 次调用/天、估值 1 次/天）。

## 实现

| 主题 | 提交 | 内容 |
|---|---|---|
| 生产稳定化 | `30e2e78` | restart/内存限额/端口收敛/备份压缩 + 数据源优化路线图 |
| 配置归位 | `334d1d9` | 业务配置全走 app_config 表，env 只留数据库凭据 |
| 运行模型 | `37876e7` | VM 只运行不编译——本地构建镜像搬运 + git-SHA 版本化 + run-only compose |
| 发布模型 | `5afb615` / `8c81fba` / `d8c24ff` | 三件套发布模型，移除被取代的旧脚本 |
| 生产基线 | `c8d0651` | **Release：首个生产基线合入 master** |
| install 修复 | `58df189` / `18af994` | 镜像包 glob 匹配 `steady-images*.tar.gz`（实际文件名无短横线）+ 测试补迁移 daily_valuation |
| Tushare 主源 | `8fb3fa9` | token 页面配置 + 六采集器优先 + 每日快照主路径 |

## 验收

- ✅ 生产只发 master：`build-release.sh` → `deploy/release/steady-<日期>-<SHA>/` → VM `./install.sh` 一键部署，幂等可重跑（对应路线图"生产只发 master，install.sh 一键部署"）
- ✅ 升级复用数据卷与密码：install.sh 升级时复用既有 postgres 项目名与 .env，不新建空卷（`8847493` / `6c3ff3b`，后续发布修复项）
- ✅ compose 稳定化四项（restart / 内存限额 / 端口收敛 / 备份压缩）落地
- ✅ 业务配置全走 `app_config`：页面可改、存库、不读环境变量（Tushare token / feishu / llm 均如此）
- ✅ Tushare 主源：日线/估值/列表/日历/指数接入，接口异常 → 降级 AkShare 不中断（`collectors/daily.py`、`finance.py` 降级链路）
- ⚠️ 降级日志为 `Tushare 失败(%s)，降级 AkShare` 字样（非原规划的 `source=akshare` 格式，但已可观测）
- ⚠️ 财务接口需 2000+ 积分，免费 120 积分档下财务实际走 AkShare（`fina_indicator` 首请求失败快速整体降级，`finance.py:105-113`）
- 🚧 **异地备份未做**（备份拷出 VM 系统盘）——路线图标注的 Iteration 0 唯一未完成项

## 遗留

1. **异地备份** 🚧：备份已在跑但只落在 VM 本机，需拷出系统盘（未排期）
2. **15-min 历史回测** ⏸：暂不做，免费源深度不够，待冲 Tushare 积分再议
3. **发布后维护记录**（本迭代后续、已解决）：
   - install 升级数据继承修复 → Release `3d08aa2`（sync/install-upgrade-master 直通，绕开 squash 历史 add/add 冲突）
   - `deploy/release/` 发布产物目录（镜像包 236M+）不入库 → `d681beb`（chore(gitignore)）
