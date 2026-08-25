package api

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"gorm.io/datatypes"
	"gorm.io/gorm"

	"quant-system/backend/internal/model"
)

// TestFactorStatsRoutes 2.3 FactorLab 路由 smoke：静态段先于通配的注册顺序（防 gin 路由树 panic）、
// 参数解析默认值、聚合响应形状（契约《因子研究闭环》§6.1）。
func TestFactorStatsRoutes(t *testing.T) {
	db := setupTestDB(t)
	seedFactorRouteData(t, db)
	r := newTestRouter(t)

	// 因子检验统计
	status, body := doJSON(t, r, "/api/v1/factors/ma_trend/stats?start=2026-01-01&end=2026-01-31&horizon=5")
	assertOK(t, status, body)
	d := dataOf(t, body)
	if d["factor"] != "ma_trend" || d["category"] != "trend" || d["horizon"].(float64) != 5 {
		t.Fatalf("stats 元数据异常: %v", d)
	}
	rg := d["range"].(map[string]any)
	if rg["start"] != "2026-01-01" || rg["end"] != "2026-01-31" {
		t.Fatalf("range 异常: %v", rg)
	}
	icSeries := d["ic_series"].([]any)
	if len(icSeries) != 2 {
		t.Fatalf("ic_series 应 2 点: %v", icSeries)
	}
	first := icSeries[0].(map[string]any)
	if first["date"] != "2026-01-05" || first["ic"] == nil {
		t.Fatalf("ic_series[0] 异常: %v", first)
	}
	if d["ic_mean"] == nil || d["monotonic"] == nil {
		t.Fatalf("聚合字段应非空: %v", d)
	}
	if d["icir"] != nil { // 仅 2 样本 < min_periods=5 → ICIR 按设计为 null
		t.Fatalf("icir 应 null（样本不足）: %v", d)
	}
	if q := d["quantiles"].([]any); len(q) != 5 {
		t.Fatalf("quantiles 应 5 组: %v", q)
	}
	if decay := d["ic_decay"].([]any); len(decay) != 5 {
		t.Fatalf("ic_decay 应 5 档: %v", decay)
	}

	// 相关矩阵
	status, body = doJSON(t, r, "/api/v1/factors/stats/correlation?start=2026-01-01&end=2026-01-31")
	assertOK(t, status, body)
	d = dataOf(t, body)
	factors := d["factors"].([]any)
	if len(factors) != 6 || factors[0] != "ma_trend" || factors[2] != "pe_ratio" {
		t.Fatalf("factors 应为 6 因子规范序: %v", factors)
	}
	matrix := d["matrix"].([]any)
	if len(matrix) != 6 || len(matrix[0].([]any)) != 6 {
		t.Fatalf("matrix 应 6×6: %v", matrix)
	}
	if v := matrix[0].([]any)[2].(float64); v != 1.0 { // ma×pe（仅 1 日，值 1.0）
		t.Fatalf("ma×pe = %v, want 1.0", v)
	}

	// 校验映射：未知因子 404 / 非法 horizon 400（静态/通配路由并存不冲突）
	status, body = doJSON(t, r, "/api/v1/factors/nope/stats")
	if status != http.StatusNotFound {
		t.Fatalf("未知因子应 404: got %d body %v", status, body)
	}
	status, body = doJSON(t, r, "/api/v1/factors/ma_trend/stats?horizon=7")
	if status != http.StatusBadRequest {
		t.Fatalf("非法 horizon 应 400: got %d body %v", status, body)
	}
}

// seedFactorRouteData 路由 smoke 种子：定义 + 2 日统计 + 1 日相关矩阵
func seedFactorRouteData(t *testing.T, db *gorm.DB) {
	t.Helper()
	day := func(s string) time.Time {
		tm, err := time.Parse("2006-01-02", s)
		if err != nil {
			t.Fatalf("种子日期解析失败 %q: %v", s, err)
		}
		return tm
	}
	if err := db.Create(&model.FactorDefinition{
		Name: "ma_trend", Category: "trend", Weight: 0.3, Version: "v1.0", Status: "active",
	}).Error; err != nil {
		t.Fatalf("seed factor_definition: %v", err)
	}
	rows := []model.FactorStat{
		{FactorName: "ma_trend", TradeDate: day("2026-01-05"),
			IC1D: f64(0.2), IC5D: f64(0.1), Q1: f64(0.05), Q5: f64(-0.03)},
		{FactorName: "ma_trend", TradeDate: day("2026-01-06"),
			IC1D: f64(0.2), IC5D: f64(0.2), Q1: f64(0.04), Q5: f64(-0.03)},
	}
	if err := db.Create(&rows).Error; err != nil {
		t.Fatalf("seed factor_stat: %v", err)
	}
	m := make([][]*float64, 6)
	for i := range m {
		m[i] = make([]*float64, 6)
	}
	m[0][0], m[0][2], m[2][0] = f64(1.0), f64(1.0), f64(1.0)
	b, _ := json.Marshal(m)
	if err := db.Create(&model.FactorCorr{TradeDate: day("2026-01-06"), Matrix: datatypes.JSON(b)}).Error; err != nil {
		t.Fatalf("seed factor_corr: %v", err)
	}
}

func f64(v float64) *float64 { return &v }
