package handler

import (
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

// factorError 因子统计业务错误映射
func factorError(err error) (int, int, string) {
	switch {
	case errors.Is(err, service.ErrFactorNotFound):
		return http.StatusNotFound, response.CodeResourceMissing, err.Error()
	case errors.Is(err, service.ErrFactorHorizon),
		errors.Is(err, service.ErrFactorRange),
		errors.Is(err, service.ErrFactorSpan):
		return http.StatusBadRequest, response.CodeInvalidParam, err.Error()
	default:
		return http.StatusInternalServerError, response.CodeInternalError, "查询失败"
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
