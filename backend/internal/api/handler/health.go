package handler

import (
	"encoding/json"
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"quant-system/backend/internal/service"
	"quant-system/backend/pkg/response"
)

// HealthCheck 健康检查：校验服务与数据库连通性
func HealthCheck(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		status := gin.H{
			"status": "ok",
			"time":   time.Now().Format(time.RFC3339),
		}

		sqlDB, err := db.DB()
		if err != nil || sqlDB.Ping() != nil {
			status["db"] = "down"
			c.JSON(http.StatusServiceUnavailable, status)
			return
		}
		status["db"] = "ok"

		c.JSON(http.StatusOK, status)
	}
}

// healthCheckDTO 数据健康单项（G5：value 为展示串，pct 为小数比例，非比例项为 null）
type healthCheckDTO struct {
	Name  string   `json:"name"`
	Value string   `json:"value"`
	Pct   *float64 `json:"pct"`
	Ok    bool     `json:"ok"`
}

// 数据健康检查中文名映射（collector 的 7 项固定检查）；未知项回退英文原名
var healthCheckLabels = map[string]string{
	"coverage":        "行情覆盖率",
	"missing_days":    "缺失交易日",
	"duplicates":      "重复数据",
	"price_anomalies": "价格异常",
	"valuation":       "估值同步",
	"financial":       "财务覆盖",
	"benchmark":       "指数基准",
}

// dataQualityDetail task_run.data_quality 的 detail 结构（与 collector 产出同构）
type dataQualityDetail struct {
	Results []struct {
		Name    string `json:"name"`
		Level   string `json:"level"`
		Message string `json:"message"`
	} `json:"results"`
	TradeDate string                     `json:"trade_date"`
	Checks    map[string]json.RawMessage `json:"check_details"`
}

// GetHealthChecks 数据健康检查（GET /health/checks）
// 数据源：collector 每日 7 项检查的 data_quality 结果（task_run.detail），无数据时返回空 items
func GetHealthChecks(taskRunSvc *service.TaskRunService) gin.HandlerFunc {
	return func(c *gin.Context) {
		run, err := taskRunSvc.GetLatest("data_quality")
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		if run == nil {
			response.OK(c, gin.H{"items": []gin.H{}, "date": ""})
			return
		}

		var detail dataQualityDetail
		if err := json.Unmarshal(run.Detail, &detail); err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "数据健康结果解析失败")
			return
		}

		items := make([]gin.H, 0, len(detail.Results))
		for _, r := range detail.Results {
			label, ok := healthCheckLabels[r.Name]
			if !ok {
				label = r.Name
			}
			items = append(items, gin.H{
				"name":  label,
				"value": healthValue(r.Message),
				"pct":   healthPct(detail.Checks, r.Name),
				"ok":    r.Level == "ok",
			})
		}

		date := detail.TradeDate
		if date == "" {
			date = formatDate(run.RunDate)
		}
		response.OK(c, gin.H{"items": items, "date": date})
	}
}

// healthValue 取 message 中「标签　值」的「值」部分（首个空白后的文本）
// 例：「行情覆盖　798/800 股票有行情（99.8%）」→「798/800 股票有行情（99.8%）」
func healthValue(msg string) string {
	if i := strings.IndexAny(msg, " \t　"); i >= 0 {
		return strings.TrimSpace(msg[i:])
	}
	return msg
}

// healthPct 取 check_details 中的比例字段（pct / coverage_pct，0-100 → 小数），无则 null
func healthPct(checks map[string]json.RawMessage, name string) *float64 {
	if checks == nil {
		return nil
	}
	var cd struct {
		Pct         *float64 `json:"pct"`
		CoveragePct *float64 `json:"coverage_pct"`
	}
	if raw, ok := checks[name]; ok && json.Unmarshal(raw, &cd) == nil {
		if cd.Pct != nil {
			v := *cd.Pct / 100
			return &v
		}
		if cd.CoveragePct != nil {
			v := *cd.CoveragePct / 100
			return &v
		}
	}
	return nil
}

// ---- G7 运维页：服务状态 + 数据资产概览 ----

// serviceRow 服务状态（G7，前端 ServiceStatus 类型：name/label/status/detail）
type serviceRow struct {
	Name   string `json:"name"`
	Label  string `json:"label"`
	Status string `json:"status"` // ok / down / unknown
	Detail string `json:"detail,omitempty"`
}

// serviceDefs 各服务探活定义：pidFile 为开发模式 host 进程 pid（相对 backend 工作目录），
// container 为 docker 容器名（生产/开发兜底）。pid 存活优先，其次 docker，否则 unknown。
var serviceDefs = []struct {
	name, label, pidFile, container string
}{
	{"backend", "backend 交易后端", "../.dev/backend.pid", "quant-backend"},
	{"collector", "collector 数据采集", "../.dev/collector.pid", "quant-collector"},
	{"quant-engine", "quant-engine 因子引擎", "../.dev/quant-engine.pid", "quant-engine"},
	{"frontend", "frontend 前端", "../.dev/vite.pid", "quant-frontend"},
	{"nginx", "nginx 网关", "", "quant-nginx"},
	{"postgres", "postgres 数据库", "", "quant-postgres"},
}

// GetServices 服务状态（GET /health/services）
// 探活顺序：host 进程 pid 存活 → docker 容器运行 → unknown；backend/postgres 用自身状态
func GetServices(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		out := make([]serviceRow, 0, len(serviceDefs))
		for _, d := range serviceDefs {
			row := serviceRow{Name: d.name, Label: d.label}
			switch d.name {
			case "backend":
				row.Status = "ok"
				row.Detail = "本服务在线"
			case "postgres":
				if sqlDB, err := db.DB(); err == nil && sqlDB.Ping() == nil {
					row.Status = "ok"
					row.Detail = "数据库连通"
				} else {
					row.Status = "down"
					row.Detail = "数据库连接失败"
				}
			default:
				row.Status, row.Detail = probeService(d.pidFile, d.container)
			}
			out = append(out, row)
		}
		response.OK(c, gin.H{"items": out})
	}
}

// probeService 探活单个服务：host pid 优先，docker 兜底，均不可得为 unknown
func probeService(pidFile, container string) (string, string) {
	if pidFile != "" && hostPidAlive(pidFile) {
		return "ok", "host 进程运行中"
	}
	if container != "" {
		if state, ok := dockerState(container); ok {
			if state == "running" {
				return "ok", "docker 容器运行中"
			}
			return "down", "docker 容器未运行"
		}
		return "unknown", "docker 不可用"
	}
	return "unknown", "无探活途径"
}

// hostPidAlive 读 pid 文件并探测进程存活（signal 0）
func hostPidAlive(path string) bool {
	data, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil || pid <= 0 {
		return false
	}
	proc, err := os.FindProcess(pid)
	if err != nil {
		return false
	}
	return proc.Signal(syscall.Signal(0)) == nil
}

// dockerState 查询容器状态；docker 命令不可用返回 (_, false)
func dockerState(name string) (string, bool) {
	out, err := exec.Command("docker", "ps", "-a", "--format", "{{.Names}} {{.State}}").Output()
	if err != nil {
		return "", false
	}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		parts := strings.Fields(line)
		if len(parts) == 2 && parts[0] == name {
			return parts[1], true
		}
	}
	return "", true // docker 可用但容器不存在
}

// GetDataAssets 数据资产概览（GET /health/data-assets）：各表精确行数
func GetDataAssets(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		var items []gin.H
		for _, table := range assetTables {
			var n int64
			if err := db.Table(table).Count(&n).Error; err != nil {
				// 单表统计失败不影响整体：如实记录 -1（调用方可识别异常）
				n = -1
			}
			items = append(items, gin.H{"table": table, "rows": n})
		}
		response.OK(c, gin.H{"items": items})
	}
}

// assetTables 数据资产表清单（public 模式全量，除纯配置表外均可入资产概览）
var assetTables = []string{
	"stock_basic", "daily_price", "daily_valuation", "financial_indicator",
	"factor_definition", "factor_value", "strategy_signal", "trade_calendar",
	"strategy", "market_hotspot", "morning_brief", "backtest_job", "backtest_result",
	"account", "account_nav", "order", "position", "trade", "task_run",
	"notify_config", "app_config",
}
