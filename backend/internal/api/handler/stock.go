package handler

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"

	"quant-system/backend/internal/repository"
	"quant-system/backend/pkg/response"
)

// GetStockList 股票列表（分页 + 行业/关键词/市场/股票池过滤 + 白名单排序）
// G2：items 增加 price/chg/amount/pe/pb/roe/score/rank/signal（缺失为 null → 前端空态）
func GetStockList(stockRepo *repository.StockRepository,
	signalRepo *repository.SignalRepository) gin.HandlerFunc {
	return func(c *gin.Context) {
		page, _ := strconv.Atoi(c.DefaultQuery("page", "1"))
		pageSize, _ := strconv.Atoi(c.DefaultQuery("page_size", "20"))
		if page < 1 {
			page = 1
		}
		if pageSize < 1 || pageSize > 100 {
			pageSize = 20
		}

		market := c.Query("market")
		if market != "" && market != "SH" && market != "SZ" && market != "BJ" {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "market 仅支持 SH/SZ/BJ")
			return
		}

		query := repository.StockListQuery{
			Page:     page,
			PageSize: pageSize,
			Industry: c.Query("industry"),
			Keyword:  strings.TrimSpace(c.Query("keyword")),
			Market:   market,
			Universe: c.Query("universe"),
			Sort:     c.Query("sort"),
			Order:    c.Query("order"),
		}

		stocks, total, err := stockRepo.GetList(query)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}

		// G2：批量补充行情/估值/财务/信号（一次 JOIN，避免 N+1）
		codes := make([]string, len(stocks))
		for i, s := range stocks {
			codes[i] = s.Code
		}
		marketData, err := stockRepo.GetPoolMarket(codes)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		val, err := stockRepo.GetPoolValuation(codes)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		fin, err := stockRepo.GetPoolFinancial(codes)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		// 信号 + 排名：仅当存在最新 multi_factor 信号日（评分池 = 当日横截面）
		var signalByCode map[string]repository.SignalBrief
		var ranks map[string]int
		latest, err := signalRepo.GetLatestSignalDate("multi_factor")
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		if latest != nil {
			signalByCode, err = signalRepo.GetSignalsByDateAndCodes(codes, *latest)
			if err != nil {
				response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
				return
			}
			ranks, err = signalRepo.GetSignalRanks("multi_factor", *latest)
			if err != nil {
				response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
				return
			}
		}

		out := make([]gin.H, 0, len(stocks))
		for _, s := range stocks {
			item := gin.H{
				"code": s.Code, "name": s.Name, "market": s.Market,
				"industry": s.Industry, "list_date": formatDate(s.ListDate),
				"status": s.Status, "universe": s.Universe,
			}
			if m, ok := marketData[s.Code]; ok {
				item["price"] = m.Price
				item["chg"] = m.Chg
				item["amount"] = m.Amount
			}
			if v, ok := val[s.Code]; ok {
				item["pe"] = v.Pe
				item["pb"] = v.Pb
			}
			if f, ok := fin[s.Code]; ok {
				item["roe"] = f.Roe
			}
			if sb, ok := signalByCode[s.Code]; ok {
				item["score"] = sb.Score
				item["signal"] = sb.Action
				if rk, ok := ranks[s.Code]; ok {
					item["rank"] = rk
				}
			}
			out = append(out, item)
		}

		response.OK(c, gin.H{
			"total":     total,
			"page":      page,
			"page_size": pageSize,
			"items":     out,
		})
	}
}
