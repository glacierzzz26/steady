package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
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

// serviceDefs 各服务探活定义（Issue #9-3 非 docker 方案）：
// pidFile 为开发模式 host 进程 pid（相对 backend 工作目录）；
// probeURL 为生产（compose 同网络 DNS）内网 HTTP 端点。pid 存活优先，其次 HTTP 探活。
var serviceDefs = []struct {
	name, label, pidFile, probeURL string
}{
	{"backend", "backend 交易后端", "../.dev/backend.pid", ""},
	{"collector", "collector 数据采集", "../.dev/collector.pid", "http://collector:9200/healthz"},
	{"quant-engine", "quant-engine 因子引擎", "../.dev/quant-engine.pid", "http://quant-engine:9201/healthz"},
	{"frontend", "frontend 前端", "../.dev/vite.pid", "http://frontend:80/"},
	{"nginx", "nginx 网关", "", "http://nginx:80/"},
	{"postgres", "postgres 数据库", "", ""},
}

// GetServices 服务状态（GET /health/services）
// 探活顺序：host 进程 pid 存活（dev）→ 内网 HTTP 探活（prod，Issue #9-3）→ unknown；
// backend/postgres 用自身状态。
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
				row.Status, row.Detail = probeService(d.pidFile, d.probeURL)
			}
			out = append(out, row)
		}
		response.OK(c, gin.H{"items": out})
	}
}

// probeService 探活单个服务：host pid 优先（dev 本地进程），HTTP 端点兜底（compose 内）
func probeService(pidFile, probeURL string) (string, string) {
	if pidFile != "" && hostPidAlive(pidFile) {
		return "ok", "host 进程运行中"
	}
	if probeURL != "" {
		return httpProbe(probeURL)
	}
	return "unknown", "无探活途径"
}

// httpProbe 内网 HTTP 探活（backend 容器无 docker CLI/socket，见 serviceDefs 注释）。
// 有 HTTP 响应（含非 2xx）即判 ok——探的是「容器在不在、服务进程活不活」；
// 连接被拒 = down（服务确实没起）；DNS 解析失败/超时 = unknown（非 compose 同网络环境）。
func httpProbe(url string) (string, string) {
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get(url)
	if err == nil {
		_, _ = io.Copy(io.Discard, resp.Body) // 排干连接体，允许复用
		_ = resp.Body.Close()
		return "ok", "内网 HTTP 探活通过"
	}
	var dnsErr *net.DNSError
	if errors.As(err, &dnsErr) {
		return "unknown", "无法解析主机（未在 compose 网络内运行）"
	}
	var nerr net.Error
	if errors.As(err, &nerr) && nerr.Timeout() {
		return "unknown", "连接超时"
	}
	if errors.Is(err, syscall.ECONNREFUSED) {
		return "down", "连接被拒（服务未运行）"
	}
	return "unknown", "HTTP 探活失败"
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
	"remediation_task", "notify_config", "app_config",
}
