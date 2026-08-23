package handler

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/gin-gonic/gin"
	"gorm.io/datatypes"

	"quant-system/backend/internal/model"
	"quant-system/backend/internal/repository"
	"quant-system/backend/internal/service"
	"quant-system/backend/pkg/response"
)

// strategyDTO 策略单条（列表/详情共用）
type strategyDTO struct {
	Name             string          `json:"name"`
	ZhName           string          `json:"zh_name"`
	Description      string          `json:"description"`
	Version          string          `json:"version"`
	Status           string          `json:"status"`
	FactorWeights    datatypes.JSON  `json:"factor_weights"`
	Params           datatypes.JSON  `json:"params"`
	LatestBacktestID uint64          `json:"latest_backtest_id"`
}

func toStrategyDTO(st service.StrategyItem) strategyDTO {
	d := strategyDTO{
		Name:          st.Name,
		ZhName:        st.ZhName,
		Description:   st.Description,
		Version:       st.Version,
		Status:        st.Status,
		FactorWeights: st.FactorWeights,
		Params:        st.Params,
		LatestBacktestID: st.LatestBacktestID,
	}
	return d
}

// GetStrategies 策略全量（GET /strategies；Iteration 4：含状态机全部状态与元数据）
func GetStrategies(strategySvc *service.StrategyService) gin.HandlerFunc {
	return func(c *gin.Context) {
		items, err := strategySvc.List()
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		out := make([]strategyDTO, 0, len(items))
		for _, it := range items {
			out = append(out, toStrategyDTO(it))
		}
		response.OK(c, gin.H{"items": out})
	}
}

// CreateStrategy 新建草稿（POST /strategies）
func CreateStrategy(strategySvc *service.StrategyService) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req service.StrategyInput
		if err := c.ShouldBindJSON(&req); err != nil || req.Name == "" {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "name 必填且请求体格式正确")
			return
		}
		st, err := strategySvc.Create(req)
		if err != nil {
			status, code, msg := strategyError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, toStrategyDTO(service.StrategyItem{Strategy: *st}))
	}
}

// UpdateStrategy 更新草稿（PUT /strategies/:name；仅 draft 可编辑）
func UpdateStrategy(strategySvc *service.StrategyService) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req service.StrategyInput
		if err := c.ShouldBindJSON(&req); err != nil {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "请求体格式错误")
			return
		}
		st, err := strategySvc.Update(c.Param("name"), req)
		if err != nil {
			status, code, msg := strategyError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, toStrategyDTO(service.StrategyItem{Strategy: *st}))
	}
}

// ForkStrategy 复制为新草稿版本（POST /strategies/:name/versions）
func ForkStrategy(strategySvc *service.StrategyService) gin.HandlerFunc {
	return func(c *gin.Context) {
		st, err := strategySvc.Fork(c.Param("name"))
		if err != nil {
			status, code, msg := strategyError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, toStrategyDTO(service.StrategyItem{Strategy: *st}))
	}
}

// SwitchStrategy 状态流转（POST /strategies/:name/switch；body {status}）
func SwitchStrategy(strategySvc *service.StrategyService) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req struct {
			Status string `json:"status"`
		}
		if err := c.ShouldBindJSON(&req); err != nil || req.Status == "" {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "status 必填")
			return
		}
		st, err := strategySvc.Switch(c.Param("name"), req.Status)
		if err != nil {
			status, code, msg := strategyError(err)
			response.Fail(c, status, code, msg)
			return
		}
		response.OK(c, toStrategyDTO(service.StrategyItem{Strategy: *st}))
	}
}

// ---- A/B 对比（§3.3） ----

// compareSideDTO A/B 单侧（策略指标 + 净值序列）
type compareSideDTO struct {
	StrategyName     string          `json:"strategy_name"`
	TotalReturn      float64         `json:"total_return"`
	AnnualizedReturn float64         `json:"annualized_return"`
	MaxDrawdown      float64         `json:"max_drawdown"`
	Sharpe           float64         `json:"sharpe"`
	Turnover         float64         `json:"turnover"`
	Cost             float64         `json:"cost"`
	Trades           int             `json:"trades"`
	Nav              []compareNavDTO `json:"nav"`
}

// compareNavDTO 对比净值点（date/nav；基准单独抽到 benchmark 块）
type compareNavDTO struct {
	Date string  `json:"date"`
	Nav  float64 `json:"nav"`
}

// compareBenchmarkDTO 基准（从 base 结果的 benchmark 点抽取，两策略共享）
type compareBenchmarkDTO struct {
	Code string          `json:"code"`
	Nav  []compareNavDTO `json:"nav"`
}

const benchmarkCode = "sh000300"

func toCompareSide(j *model.BacktestJob) compareSideDTO {
	d := compareSideDTO{StrategyName: j.StrategyName}
	if j.Result != nil {
		d.TotalReturn = j.Result.TotalReturn
		d.AnnualizedReturn = j.Result.AnnualizedReturn
		d.MaxDrawdown = j.Result.MaxDrawdown
		d.Sharpe = j.Result.Sharpe
		d.Turnover = j.Result.Turnover
		d.Cost = j.Result.Cost
		d.Trades = j.Result.Trades
		var pts []navPointDTO
		_ = json.Unmarshal([]byte(j.Result.Nav), &pts)
		for _, p := range pts {
			d.Nav = append(d.Nav, compareNavDTO{Date: p.Date, Nav: p.Nav})
		}
	}
	return d
}

func toCompareBenchmark(j *model.BacktestJob) compareBenchmarkDTO {
	b := compareBenchmarkDTO{Code: benchmarkCode}
	if j.Result != nil {
		var pts []navPointDTO
		_ = json.Unmarshal([]byte(j.Result.Nav), &pts)
		for _, p := range pts {
			if p.Benchmark != nil {
				b.Nav = append(b.Nav, compareNavDTO{Date: p.Date, Nav: *p.Benchmark})
			}
		}
	}
	return b
}

// CompareStrategies A/B 对比（GET /strategies/compare?base=&candidate=&start=&end=&fill_mode=）
// 任务未完成 → {status:"pending"} 前端轮询；done → {base, candidate, benchmark}
func CompareStrategies(strategySvc *service.StrategyService) gin.HandlerFunc {
	return func(c *gin.Context) {
		base := c.Query("base")
		candidate := c.Query("candidate")
		if base == "" || candidate == "" {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "base/candidate 必填")
			return
		}
		start, err := parseDate(c.Query("start"))
		if err != nil {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "start 格式应为 YYYY-MM-DD")
			return
		}
		end, err := parseDate(c.Query("end"))
		if err != nil {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "end 格式应为 YYYY-MM-DD")
			return
		}
		out, err := strategySvc.Compare(base, candidate, start, end, c.Query("fill_mode"))
		if err != nil {
			status, code, msg := strategyError(err)
			response.Fail(c, status, code, msg)
			return
		}
		if out.Status != "done" {
			response.OK(c, gin.H{"status": out.Status})
			return
		}
		response.OK(c, gin.H{
			"base":      toCompareSide(out.Base),
			"candidate": toCompareSide(out.Cand),
			"benchmark": toCompareBenchmark(out.Base),
		})
	}
}

// GetFactors 因子定义列表（GET /factors；StrategyFactory 构建器因子池）
func GetFactors(repo *repository.StrategyRepository) gin.HandlerFunc {
	return func(c *gin.Context) {
		items, err := repo.GetFactors()
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		response.OK(c, gin.H{"items": items})
	}
}

// strategyError 策略业务错误映射
func strategyError(err error) (int, int, string) {
	switch {
	case errors.Is(err, service.ErrStrategyNotFound):
		return http.StatusNotFound, response.CodeResourceMissing, err.Error()
	case errors.Is(err, service.ErrStrategyExists):
		return http.StatusBadRequest, response.CodeInvalidParam, err.Error()
	case errors.Is(err, service.ErrStrategyNotDraft),
		errors.Is(err, service.ErrStrategyTransition),
		errors.Is(err, service.ErrStrategyArchived),
		errors.Is(err, service.ErrStrategyInvalidData):
		return http.StatusBadRequest, response.CodeInvalidParam, err.Error()
	default:
		return http.StatusInternalServerError, response.CodeInternalError, "操作失败"
	}
}
