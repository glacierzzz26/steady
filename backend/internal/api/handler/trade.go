package handler

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"

	"quant-system/backend/internal/repository"
	"quant-system/backend/pkg/response"
)

// GetTrades 成交列表（GET /trades?page=&page_size=）
// G4：items 增加 name（join stock_basic）
func GetTrades(tradeRepo *repository.TradeRepository,
	accountRepo *repository.AccountRepository,
	stockRepo *repository.StockRepository) gin.HandlerFunc {

	return func(c *gin.Context) {
		acc, err := accountRepo.GetPrimary()
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		page, _ := strconv.Atoi(c.DefaultQuery("page", "1"))
		if page < 1 {
			page = 1
		}
		pageSize, _ := strconv.Atoi(c.DefaultQuery("page_size", "20"))
		if pageSize < 1 {
			pageSize = 20
		}
		if pageSize > 100 {
			pageSize = 100
		}

		items, total, err := tradeRepo.GetList(acc.ID, page, pageSize)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		// G4：批量补股票名称（一次 JOIN 避免 N+1）
		codes := make([]string, len(items))
		for i, t := range items {
			codes[i] = t.Code
		}
		names, err := stockRepo.GetNames(codes)
		if err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "查询失败")
			return
		}
		out := make([]tradeDTO, 0, len(items))
		for _, t := range items {
			out = append(out, tradeDTO{
				TradeID:    t.TradeID,
				OrderID:    t.OrderID,
				Code:       t.Code,
				Name:       names[t.Code],
				Direction:  t.Direction,
				Price:      t.Price,
				Quantity:   t.Quantity,
				Amount:     t.Amount,
				Commission: t.Commission,
				Tax:        t.Tax,
				NetAmount:  t.NetAmount,
				TradeDate:  formatDate(t.TradeDate),
			})
		}
		response.OK(c, gin.H{"items": out, "total": total, "page": page, "page_size": pageSize})
	}
}
