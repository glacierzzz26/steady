package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"

	"quant-system/backend/internal/service"
	"quant-system/backend/pkg/response"
)

// factorStatsDefaultYears 无 start/end 时默认回看区间（对齐前端 FactorLab 默认 2 年）
const factorStatsDefaultYears = 2

// parseFactorRange 解析 start/end/horizon（均可省略）
// start 默认 end-2 年、end 默认今天（服务端本地日）、horizon 默认 5。
func parseFactorRange(c *gin.Context) (start, end time.Time, horizon int, err error) {
	horizon = 5
	if h := c.Query("horizon"); h != "" {
		horizon, err = strconv.Atoi(h)
		if err != nil {
			return time.Time{}, time.Time{}, 0, errors.New("horizon 应为数字")
		}
	}
	end = time.Now().Truncate(24 * time.Hour)
	if s := c.Query("end"); s != "" {
		end, err = parseDate(s)
		if err != nil {
			return time.Time{}, time.Time{}, 0, errors.New("end 格式应为 YYYY-MM-DD")
		}
	}
	start = end.AddDate(0, 0, -factorStatsDefaultYears*365)
	if s := c.Query("start"); s != "" {
		start, err = parseDate(s)
		if err != nil {
			return time.Time{}, time.Time{}, 0, errors.New("start 格式应为 YYYY-MM-DD")
		}
	}
	return start, end, horizon, nil
}

// parseFactorDates 解析 start/end（correlation 无 horizon；缺省同 parseFactorRange）
func parseFactorDates(c *gin.Context) (start, end time.Time, err error) {
	end = time.Now().Truncate(24 * time.Hour)
	if s := c.Query("end"); s != "" {
		end, err = parseDate(s)
		if err != nil {
			return time.Time{}, time.Time{}, errors.New("end 格式应为 YYYY-MM-DD")
		}
	}
	start = end.AddDate(0, 0, -factorStatsDefaultYears*365)
	if s := c.Query("start"); s != "" {
		start, err = parseDate(s)
		if err != nil {
			return time.Time{}, time.Time{}, errors.New("start 格式应为 YYYY-MM-DD")
		}
	}
	return start, end, nil
}

// factorError 因子统计/生命周期/试算业务错误映射
func factorError(err error) (int, int, string) {
	switch {
	case errors.Is(err, service.ErrFactorNotFound),
		errors.Is(err, service.ErrFactorTrialNotFound):
		return http.StatusNotFound, response.CodeResourceMissing, err.Error()
	case errors.Is(err, service.ErrFactorHorizon),
		errors.Is(err, service.ErrFactorRange),
		errors.Is(err, service.ErrFactorSpan),
		errors.Is(err, service.ErrFactorExists),
		errors.Is(err, service.ErrFactorNotEditable),
		errors.Is(err, service.ErrFactorNotDeletable),
		errors.Is(err, service.ErrFactorHasRefs),
		errors.Is(err, service.ErrFactorTransition),
		errors.Is(err, service.ErrFactorTrialRange),
		errors.Is(err, service.ErrFactorTrialSpan),
		errors.Is(err, service.ErrFactorTrialGrid),
		errors.Is(err, service.ErrFactorTrialParams):
		return http.StatusBadRequest, response.CodeInvalidParam, err.Error()
	default:
		return http.StatusInternalServerError, response.CodeInternalError, "操作失败"
	}
}

// GetFactorStats 因子检验统计（GET /factors/:name/stats?start=&end=&horizon=）
// 契约《因子研究闭环》§6.1：ic_series/icir/ic_mean/ic_std/ic_decay/quantiles/monotonic。
func GetFactorStats(statsSvc *service.FactorStatsService) gin.HandlerFunc {
	return func(c *gin.Context) {
		start, end, horizon, err := parseFactorRange(c)
		if err != nil {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, err.Error())
			return
		}
		out, err := statsSvc.Stats(c.Param("name"), start, end, horizon)
		if err != nil {
			status, code, msg := factorError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, out)
	}
}

// GetFactorCorrelation 6 因子相关矩阵（GET /factors/stats/correlation?start=&end=）
// 契约 §6.1：{"factors": 6 因子规范序, "matrix": 6×6 区间均值（无共现格为 null）}。
func GetFactorCorrelation(statsSvc *service.FactorStatsService) gin.HandlerFunc {
	return func(c *gin.Context) {
		start, end, err := parseFactorDates(c)
		if err != nil {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, err.Error())
			return
		}
		out, err := statsSvc.Correlation(start, end)
		if err != nil {
			status, code, msg := factorError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, out)
	}
}

// ---- G10 FactorFactory 生命周期（2.3b，契约 §6.2）----

// ListFactors 因子定义列表（GET /factors；FactorLab 卡片 + StrategyFactory 构建器 + FactorFactory 管理）
func ListFactors(factorSvc *service.FactorService) gin.HandlerFunc {
	return func(c *gin.Context) {
		items, err := factorSvc.List()
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		response.OK(c, gin.H{"items": items})
	}
}

// CreateFactor 新建草稿因子（POST /factors；body 见 service.FactorInput）
func CreateFactor(factorSvc *service.FactorService) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req service.FactorInput
		if err := c.ShouldBindJSON(&req); err != nil || req.Name == "" {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "name 必填且请求体格式正确")
			return
		}
		f, err := factorSvc.Create(req)
		if err != nil {
			status, code, msg := factorError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, f)
	}
}

// UpdateFactor 编辑因子（PUT /factors/:name；仅草稿/停用可改）
func UpdateFactor(factorSvc *service.FactorService) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req service.FactorInput
		if err := c.ShouldBindJSON(&req); err != nil {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "请求体格式错误")
			return
		}
		f, err := factorSvc.Update(c.Param("name"), req)
		if err != nil {
			status, code, msg := factorError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, f)
	}
}

// ForkFactor 复制为新草稿版本（POST /factors/:name/versions；v1.0→v2.0，含 params 快照）
func ForkFactor(factorSvc *service.FactorService) gin.HandlerFunc {
	return func(c *gin.Context) {
		f, err := factorSvc.Fork(c.Param("name"))
		if err != nil {
			status, code, msg := factorError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, f)
	}
}

// SwitchFactor 状态流转（POST /factors/:name/switch；body {status}，状态机见 service）
func SwitchFactor(factorSvc *service.FactorService) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req struct {
			Status string `json:"status"`
		}
		if err := c.ShouldBindJSON(&req); err != nil || req.Status == "" {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "status 必填")
			return
		}
		f, err := factorSvc.Switch(c.Param("name"), req.Status)
		if err != nil {
			status, code, msg := factorError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, f)
	}
}

// DeleteFactor 删除因子（DELETE /factors/:name；仅草稿，有试算/评分/检验记录则拒）
func DeleteFactor(factorSvc *service.FactorService) gin.HandlerFunc {
	return func(c *gin.Context) {
		if err := factorSvc.Delete(c.Param("name")); err != nil {
			status, code, msg := factorError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, gin.H{"deleted": c.Param("name")})
	}
}

// ---- G10 试算/寻优任务（2.3b，契约 §6.2）----

// CreateFactorTrial 提交单组参数试算（POST /factors/:name/trial；body {params, start, end}）
func CreateFactorTrial(factorTrialSvc *service.FactorTrialService) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req service.FactorTrialInput
		if err := c.ShouldBindJSON(&req); err != nil {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "请求体格式错误")
			return
		}
		t, err := factorTrialSvc.CreateTrial(c.Param("name"), req)
		if err != nil {
			status, code, msg := factorError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, gin.H{"id": t.ID, "status": t.Status})
	}
}

// CreateFactorOptimize 提交参数寻优（POST /factors/:name/optimize；body {param_grid, start, end}）
func CreateFactorOptimize(factorTrialSvc *service.FactorTrialService) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req service.FactorOptimizeInput
		if err := c.ShouldBindJSON(&req); err != nil {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "请求体格式错误")
			return
		}
		t, err := factorTrialSvc.CreateOptimize(c.Param("name"), req)
		if err != nil {
			status, code, msg := factorError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, gin.H{"id": t.ID, "status": t.Status})
	}
}

// ListFactorTrials 试算任务列表（GET /factor-trials?factor_name=&limit=；FactorFactory 版本列表/试算历史）
func ListFactorTrials(factorTrialSvc *service.FactorTrialService) gin.HandlerFunc {
	return func(c *gin.Context) {
		limit, _ := strconv.Atoi(c.Query("limit"))
		items, err := factorTrialSvc.List(c.Query("factor_name"), limit)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		response.OK(c, gin.H{"items": items})
	}
}

// GetFactorTrial 试算任务详情（GET /factor-trials/:id）
// pending/running → {status}；failed → {status, error}；done → {status, ...result}
func GetFactorTrial(factorTrialSvc *service.FactorTrialService) gin.HandlerFunc {
	return func(c *gin.Context) {
		id, err := strconv.ParseUint(c.Param("id"), 10, 64)
		if err != nil {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "id 应为数字")
			return
		}
		t, err := factorTrialSvc.Get(id)
		if err != nil {
			status, code, msg := factorError(err)
			response.Fail(c, status, code, msg)
			return
		}
		if t.Status != "done" {
			out := gin.H{"status": t.Status}
			if t.Status == "failed" {
				out["error"] = t.Error
			}
			response.OK(c, out)
			return
		}
		// done：result JSONB 展开合并到顶层（trial: ic_series/icir/quantiles/monotonic/heatmap?
		// optimize: windows/horizons/grid）
		out := gin.H{"status": t.Status}
		if len(t.Result) > 0 {
			var m map[string]any
			if err := json.Unmarshal(t.Result, &m); err == nil {
				for k, v := range m {
					out[k] = v
				}
			}
		}
		response.OK(c, out)
	}
}
