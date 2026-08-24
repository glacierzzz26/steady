package handler

import (
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"quant-system/backend/internal/service"
	"quant-system/backend/pkg/response"
)

// GetMarketStatus 市场状态（GET /market/status；右上角开市/休市 chip 数据源）。
// 由 trade_calendar 判定今日是否交易日 + 按钟点给出盘中/休市阶段。
func GetMarketStatus(svc *service.MarketStatusService) gin.HandlerFunc {
	return func(c *gin.Context) {
		st, err := svc.GetStatus(time.Now())
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		response.OK(c, st)
	}
}
