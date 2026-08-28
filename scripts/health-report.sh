#!/usr/bin/env bash
# ===== Steady 一键体检（health-report）=====
#
# 一条命令打出系统全貌，适合不懂代码的人先跑它定位问题。
#
# 用法：
#   本地仓库：   cd /path/to/steady && ./scripts/health-report.sh
#   生产 VM：    进发布目录（含 .env + docker-compose.run.yml）后：
#                cd ~/steady-<版本> && ./scripts/health-report.sh
#
# 输出 8 节中文体检报告 + 异常项汇总；单项失败不中断、不阻塞，末尾按有无异常退出码。
# 与 backup-db.sh / migrate.sh 同一套约定：docker exec 容器内本地 trust，免密码。
#
# 可覆盖环境变量：PG_CONTAINER（默认 quant-postgres）、DB_USER（默认 quant）、DB_NAME（默认 quant_system）。
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------- 颜色（仅 TTY 上色；管道/重定向时自动禁用） ----------
if [ -t 1 ]; then
  C_G=$'\033[32m'; C_R=$'\033[31m'; C_Y=$'\033[33m'; C_C=$'\033[36m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=""; C_R=""; C_Y=""; C_C=""; C_B=""; C_0=""
fi
OK()   { echo "${C_G}✅ ${1}${C_0}"; }
WARN() { echo "${C_Y}⚠️ ${1}${C_0}"; }
ERR()  { echo "${C_R}❌ ${1}${C_0}"; }
INFO() { echo "${C_C}▸ ${1}${C_0}"; }
HDR()  { echo; echo "${C_B}═══ ${1} ═══${C_0}"; }

PROBLEMS=()
prob() { PROBLEMS+=("$1"); }
TAB=$'\t'

# ---------- 定位 .env 与凭据（同 migrate.sh） ----------
for envf in "$PWD/.env" "$SCRIPT_DIR/../.env"; do
  if [ -f "$envf" ]; then
    line=$(grep -E '^(DB_USER|DB_NAME)=' "$envf" || true)
    [ -n "$line" ] && export $(echo "$line" | xargs)
  fi
done
DB_USER="${DB_USER:-quant}"
DB_NAME="${DB_NAME:-quant_system}"
PG_CONTAINER="${PG_CONTAINER:-quant-postgres}"

# ---------- DB 工具 ----------
pg() { docker exec "$PG_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tA -F'|' -c "$1" 2>/dev/null; }

echo
echo "${C_B}════════ Steady 系统体检 · $(date '+%Y-%m-%d %H:%M:%S') ════════${C_0}"

# ---------- 1. 容器状态 ----------
HDR "1/8 容器状态"
if ! command -v docker >/dev/null 2>&1; then
  ERR "docker 命令不可用（请在装有 docker 的主机 / 发布目录运行）"
  prob "docker 命令不可用"
  NO_DOCKER=1
  DB_OK=0
else
  NO_DOCKER=0
  ps_out=$(docker ps -a --format '{{.Names}}|{{.Status}}')
  for c in quant-postgres quant-collector quant-engine quant-backend quant-frontend quant-nginx; do
    if line=$(grep -E "^${c}\|" <<<"$ps_out"); then
      st="${line#*|}"
      case "$st" in
        Up*) OK "$c  ${st}" ;;
        *)   ERR "$c  ${st}（未运行）"; prob "容器 $c 未运行（$st）" ;;
      esac
    else
      ERR "$c  容器不存在"; prob "容器 $c 不存在"
    fi
  done
fi

# ---------- 2. 数据库连通 ----------
HDR "2/8 数据库连通"
if [ "$NO_DOCKER" = 1 ]; then
  ERR "docker 不可用，DB 检查跳过（后续数据相关节全部跳过）"
  prob "docker 不可用，DB 检查跳过"
  DB_OK=0
elif docker exec "$PG_CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
  OK "quant-postgres 接受连接（${DB_USER}@${DB_NAME}）"
  DB_OK=1
else
  ERR "数据库不可达（容器未运行或 DB 凭据不对）；后续数据相关节全部跳过"
  prob "数据库不可达"
  DB_OK=0
fi

# ---------- 3. 最近失败任务 ----------
HDR "3/8 最近失败任务（近 3 天）"
if [ "$DB_OK" = 1 ]; then
  rows=$(pg "SELECT run_date||'  '||task_name||'  '||left(message,80)
             FROM task_run WHERE status='failed' AND run_date >= current_date - 3
             ORDER BY run_date DESC, id DESC LIMIT 20")
  if [ -z "${rows:-}" ]; then
    OK "无失败任务记录"
  else
    while IFS= read -r r; do [ -n "$r" ] && ERR "  $r"; done <<<"$rows"
    prob "近 3 天有失败任务（见上方列表）"
  fi
else
  INFO "跳过（DB 不可达）"
fi

# ---------- 4. 数据新鲜度 ----------
HDR "4/8 数据新鲜度（最新日期）"
if [ "$DB_OK" = 1 ]; then
  last_trade=$(pg "SELECT COALESCE(max(trade_date)::text,'') FROM daily_price WHERE code NOT LIKE 'sh%'")
  INFO "最近行情交易日：${last_trade:-无}"
  fresh=$(pg "SELECT 'daily_price',       COALESCE(max(trade_date)::text,'')      FROM daily_price WHERE code NOT LIKE 'sh%'
          UNION ALL SELECT 'daily_valuation',  COALESCE(max(trade_date)::text,'')  FROM daily_valuation
          UNION ALL SELECT 'financial_announce',COALESCE(max(announce_date)::text,'') FROM financial_indicator
          UNION ALL SELECT 'factor_value',     COALESCE(max(trade_date)::text,'')  FROM factor_value
          UNION ALL SELECT 'strategy_signal',  COALESCE(max(trade_date)::text,'')  FROM strategy_signal
          UNION ALL SELECT 'account_nav',      COALESCE(max(trade_date)::text,'')  FROM account_nav
          UNION ALL SELECT 'morning_brief',    COALESCE(max(brief_date)::text,'')  FROM morning_brief
          ORDER BY 1")
  while IFS='|' read -r tname tdate; do
    [ -z "$tname" ] && continue
    case "$tname" in
      daily_price)        label="行情"; cmp=1 ;;
      daily_valuation)    label="估值"; cmp=1 ;;
      factor_value)       label="因子"; cmp=1 ;;
      strategy_signal)    label="信号"; cmp=1 ;;
      financial_announce) label="财务(公告日)"; cmp=0 ;;
      account_nav)        label="净值"; cmp=0 ;;
      morning_brief)      label="早报"; cmp=0 ;;
      *) label="$tname"; cmp=0 ;;
    esac
    if [ "$cmp" = 1 ]; then
      if [ -z "$tdate" ]; then
        ERR "  ${label}${TAB}（无数据）"; prob "表 ${tname} 无数据"
      elif [ "$tdate" = "$last_trade" ]; then
        OK "  ${label}${TAB}${tdate}"
      else
        WARN "  ${label}${TAB}${tdate}（最近交易日 ${last_trade}）"
      fi
    else
      if [ -z "$tdate" ]; then WARN "  ${label}${TAB}（暂无记录）"; else INFO "  ${label}${TAB}${tdate}"; fi
    fi
  done <<<"$fresh"
else
  INFO "跳过（DB 不可达）"
fi

# ---------- 5. 因子 / 信号 / 净值 ----------
HDR "5/8 因子 / 信号 / 净值"
if [ "$DB_OK" = 1 ]; then
  fv_date=$(pg "SELECT max(trade_date) FROM factor_value")
  fv_rows=$(pg "SELECT count(*) FROM factor_value WHERE trade_date=(SELECT max(trade_date) FROM factor_value)")
  [ -z "$fv_rows" ] && fv_rows=0
  pool=$(pg "SELECT count(*) FROM stock_basic WHERE universe <> ''")
  [ -z "$pool" ] && pool=0
  expect=$((6 * pool))
  INFO "因子 factor_value  最新 ${fv_date:-空} · ${fv_rows} 行（预期 ≈ ${expect}）"
  if [ "$expect" -gt 0 ] && [ "$fv_rows" -lt $((expect * 90 / 100)) ]; then
    WARN "  因子行数明显偏少，注意检查"; prob "factor_value 行数偏少（${fv_rows}/${expect}）"
  fi

  sg_date=$(pg "SELECT max(trade_date) FROM strategy_signal")
  sg_rows=$(pg "SELECT count(*) FROM strategy_signal WHERE trade_date=(SELECT max(trade_date) FROM strategy_signal)")
  [ -z "$sg_rows" ] && sg_rows=0
  INFO "信号 strategy_signal  最新 ${sg_date:-空} · ${sg_rows} 条"
  if [ -n "${sg_date:-}" ] && [ "$sg_rows" -lt 100 ]; then
    WARN "  信号条数偏少，注意检查"; prob "strategy_signal 条数偏少（${sg_rows}）"
  fi

  nav=$(pg "SELECT trade_date||'|'||nav||'|'||daily_return||'|'||total_asset FROM account_nav ORDER BY trade_date DESC LIMIT 1")
  if [ -n "${nav:-}" ]; then
    IFS='|' read -r nd nnav dret tasset <<<"$nav"
    pct=$(awk -v v="$dret" 'BEGIN{printf "%.2f%%", v*100}')
    total=$(printf "%'.0f" "$tasset" 2>/dev/null || echo "$tasset")
    OK "净值 account_nav  最新 ${nd} · 净值 ${nnav} · 日收益 ${pct} · 总资产 ${total}"
  else
    WARN "净值 account_nav  暂无记录"
  fi
else
  INFO "跳过（DB 不可达）"
fi

# ---------- 6. 数据质量检查 ----------
HDR "6/8 数据质量检查（最近一次）"
if [ "$DB_OK" = 1 ]; then
  dq=$(pg "SELECT d.trade_date, r->>'name', r->>'level', r->>'message'
           FROM (SELECT run_date::text AS trade_date, detail
                 FROM task_run WHERE task_name='data_quality'
                 ORDER BY run_date DESC, id DESC LIMIT 1) d,
                jsonb_array_elements(d.detail->'results') r")
  if [ -z "${dq:-}" ]; then
    WARN "暂无 data_quality 台账"; prob "无 data_quality 检查记录"
  else
    while IFS='|' read -r dqdate name lvl msg; do
      [ -z "$name" ] && continue
      case "$lvl" in
        ok)   OK   "${name}: ${msg}" ;;
        warn) WARN "${name}: ${msg}" ;;
        fail) ERR  "${name}: ${msg}" ;;
      esac
    done <<<"$dq"
    fails=$(awk -F'|' '$3=="fail"{print $2}' <<<"$dq")
    warns=$(awk -F'|' '$3=="warn"{print $2}' <<<"$dq")
    dqdate=$(head -1 <<<"$dq" | cut -d'|' -f1)
    [ -n "$dqdate" ] && INFO "检查交易日 ${dqdate}"
    if [ -n "$fails" ]; then
      prob "数据质量不通过: $(echo "$fails" | tr '\n' ' ')"
    fi
    if [ -n "$warns" ]; then
      INFO "另有警告项: $(echo "$warns" | tr '\n' ' ')"
    fi
  fi
else
  INFO "跳过（DB 不可达）"
fi

# ---------- 7. 磁盘与备份 ----------
HDR "7/8 磁盘与备份"
if [ "$NO_DOCKER" = 0 ] && [ "$DB_OK" = 1 ]; then
  sz=$(docker exec "$PG_CONTAINER" du -sh "$(docker exec "$PG_CONTAINER" sh -c 'echo ${PGDATA:-/var/lib/postgresql/data}')" 2>/dev/null | cut -f1)
  [ -n "${sz:-}" ] && OK "postgres 数据目录  ${sz}" || WARN "无法获取 postgres 数据目录大小"
else
  INFO "跳过 DB 大小（docker/DB 不可达）"
fi
if command -v df >/dev/null 2>&1; then
  use=$(df -h / | tail -1 | awk '{print $5+0}')
  line=$(df -h / | tail -1 | awk '{print "宿主根分区  "$6"  已用 "$3" / "$2"（使用率 "$5"）"}')
  echo "  ${C_C}${line}${C_0}"
  if [ "${use:-0}" -ge 90 ]; then WARN "  根分区使用率 ${use}% 偏高，请清理"; prob "根分区使用率 ${use}%"; fi
fi
bdir="$SCRIPT_DIR/../backup"
if [ -d "$bdir" ]; then
  cnt=$(find "$bdir" -name '*.sql.gz' | wc -l | tr -d ' ')
  latest=$(ls -t "$bdir"/*.sql.gz 2>/dev/null | head -1)
  if [ -n "$latest" ]; then
    OK "备份  ${cnt} 份 · 最新 $(basename "$latest") · $(du -h "$latest" | cut -f1)"
  else
    WARN "backup/ 目录为空，无备份"; prob "backup/ 目录为空"
  fi
else
  WARN "未找到备份目录（$bdir）"; prob "未找到备份目录"
fi

# ---------- 8. schema 漂移 ----------
HDR "8/8 schema 漂移（init.sql vs 现库）"
if [ -f "$SCRIPT_DIR/migrate.sh" ]; then
  tmp=$(mktemp)
  if "$SCRIPT_DIR/migrate.sh" --check >"$tmp" 2>&1; then
    grep -v '^$' "$tmp"
  else
    grep -v '^$' "$tmp"
    ERR "schema 漂移：请新增 deploy/migrations/NNN_*.sql 幂等迁移后重跑 migrate.sh"
    prob "schema 漂移"
  fi
  rm -f "$tmp"
else
  INFO "未找到 scripts/migrate.sh（跳过；生产发布目录应包含）"
fi

# ---------- 汇总 ----------
HDR "体检汇总"
if [ "${#PROBLEMS[@]}" -eq 0 ]; then
  OK "全部正常，无异常项"
  exit 0
else
  echo "发现 ${#PROBLEMS[@]} 项异常："
  i=1
  for p in "${PROBLEMS[@]}"; do echo "  ${C_R}${i}) ${p}${C_0}"; i=$((i + 1)); done
  echo
  INFO "先看上方对应节，再按 docs/排障手册.md 的「症状→定位→处置」处理"
  exit 1
fi
