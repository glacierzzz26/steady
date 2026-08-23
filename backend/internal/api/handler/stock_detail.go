package handler

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"quant-system/backend/internal/repository"
	"quant-system/backend/pkg/response"
)

// GetStockDetail 股票详情：基本信息 + 最新行情 + 财务摘要 + 日度估值 + 因子得分（公告日最新）
// 仅股票不存在返回 404；行情/财务/估值/因子未回填时对应字段为 null（仍 200）
func GetStockDetail(stockRepo *repository.StockRepository,
	dailyRepo *repository.DailyRepository,
	financialRepo *repository.FinancialRepository,
	signalRepo *repository.SignalRepository) gin.HandlerFunc {

	return func(c *gin.Context) {
		code := c.Param("code")
		if !validCode(code) {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "股票代码格式错误")
			return
		}

		stock, err := stockRepo.GetByCode(code)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		if stock == nil {
			response.Fail(c, http.StatusNotFound, response.CodeResourceMissing, "股票不存在")
			return
		}

		latest, err := dailyRepo.GetLatest(code)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		fin, err := financialRepo.GetLatestByAnnounce(code)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		val, err := dailyRepo.GetLatestValuation(code)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}

		dto := stockDetailDTO{StockBasicDTO: toStockBasicDTO(*stock)}
		if latest != nil {
			item := toKlineItem(*latest)
			dto.LatestBar = &item
		}
		if fin != nil {
			f := toFinancialDTO(*fin)
			dto.FinancialSummary = &f
		}
		if val != nil {
			dto.Valuation = &valuationDTO{
				TradeDate: formatDate(val.TradeDate),
				PeTtm:     val.PeTtm,
				Pb:        val.Pb,
			}
		}
		// G3：因子得分（最新信号 + 排名 + 四因子分项；无信号时为 null）
		fs, err := signalRepo.GetStockFactorScore(code)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		if fs != nil {
			dto.FactorScore = &factorScoreDTO{
				Score:   fs.Score,
				Rank:    fs.Rank,
				Signal:  fs.Signal,
				Trend:   fs.Trend,
				Value:   fs.Value,
				Quality: fs.Quality,
				Risk:    fs.Risk,
			}
		}
		response.OK(c, dto)
	}
}
