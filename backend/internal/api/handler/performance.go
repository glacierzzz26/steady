package handler

import (
	"encoding/json"
	"net/http"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"quant-system/backend/internal/model"
	"quant-system/backend/pkg/response"
)

// GetPerformanceHitRate 信号命中率（GET /performance/hit-rate?strategy=&window=）
//
// 只读 strategy_perf（metric_type='hit_rate'），取该策略最近一期预计算结果。
// detail.windows 为 {5,10,20} 各窗口命中率；window 参数可单选某窗口。
// 无数据/窗口内样本不足 → 前端 Empty / "待积累" 兜底，诚实展示。
func GetPerformanceHitRate(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		strategy := c.DefaultQuery("strategy", "multi_factor")
		window := c.Query("window") // 可选：5/10/20，缺省返回全部窗口
		if window != "" && window != "5" && window != "10" && window != "20" {
			response.Fail(c, http.StatusBadRequest, response.CodeInvalidParam, "window 参数错误（5/10/20）")
			return
		}
		var row model.StrategyPerf
		if err := db.Where("strategy_name = ? AND metric_type = ?",
			strategy, "hit_rate").
			Order("period_end DESC, id DESC").First(&row).Error; err != nil {
			response.Fail(c, http.StatusNotFound, response.CodeResourceMissing, "暂无命中率数据（每日 21:20 预计算）")
			return
		}
		var detail map[string]interface{}
		if err := json.Unmarshal([]byte(row.Detail), &detail); err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "命中率数据解析失败")
			return
		}
		out := gin.H{
			"strategy_name": row.StrategyName,
			"period_start":  formatDate(row.PeriodStart),
			"period_end":    formatDate(row.PeriodEnd),
			"detail":        detail,
		}
		if window != "" {
			if w, ok := detail["windows"].(map[string]interface{})[window]; ok {
				out["window"] = w
			} else {
				response.Fail(c, http.StatusNotFound, response.CodeResourceMissing, "该窗口暂无样本")
				return
			}
		}
		response.OK(c, out)
	}
}

// GetPerformanceNavOverlay 实盘vs回测vs基准对照（GET /performance/nav-overlay?strategy=）
//
// 只读 strategy_perf（metric_type='nav_overlay'），返回 series + metrics。
func GetPerformanceNavOverlay(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		strategy := c.DefaultQuery("strategy", "multi_factor")
		var row model.StrategyPerf
		if err := db.Where("strategy_name = ? AND metric_type = ?",
			strategy, "nav_overlay").
			Order("period_end DESC, id DESC").First(&row).Error; err != nil {
			response.Fail(c, http.StatusNotFound, response.CodeResourceMissing, "暂无对照数据（每日 21:20 预计算）")
			return
		}
		var detail map[string]interface{}
		if err := json.Unmarshal([]byte(row.Detail), &detail); err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "对照数据解析失败")
			return
		}
		response.OK(c, gin.H{
			"strategy_name": row.StrategyName,
			"period_start":  formatDate(row.PeriodStart),
			"period_end":    formatDate(row.PeriodEnd),
			"series":        detail["series"],
			"metrics":       detail["metrics"],
		})
	}
}

// GetPerformanceAttribution 因子贡献归因（GET /performance/attribution?strategy=）
//
// 只读 strategy_perf（metric_type='attribution'），返回组合超额收益的因子分解：
// detail.daily（逐日 excess/各因子 contrib/residual）+ detail.monthly（月度聚合）
// + detail.live（主账户 daily_return 对照）。归因对象 = 策略信号组合，口径见
// quant-engine/app/performance.py::compute_attribution。
func GetPerformanceAttribution(db *gorm.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		strategy := c.DefaultQuery("strategy", "multi_factor")
		var row model.StrategyPerf
		if err := db.Where("strategy_name = ? AND metric_type = ?",
			strategy, "attribution").
			Order("period_end DESC, id DESC").First(&row).Error; err != nil {
			response.Fail(c, http.StatusNotFound, response.CodeResourceMissing, "暂无归因数据（每日 21:20 预计算）")
			return
		}
		var detail map[string]interface{}
		if err := json.Unmarshal([]byte(row.Detail), &detail); err != nil {
			response.Fail(c, http.StatusInternalServerError, response.CodeInternalError, "归因数据解析失败")
			return
		}
		response.OK(c, gin.H{
			"strategy_name": row.StrategyName,
			"period_start":  formatDate(row.PeriodStart),
			"period_end":    formatDate(row.PeriodEnd),
			"detail":        detail,
		})
	}
}
