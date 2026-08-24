package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"

	"quant-system/backend/internal/model"
	"quant-system/backend/internal/repository"
	"quant-system/backend/internal/service"
	"quant-system/backend/pkg/response"
)

// marketHTTPDB 市场状态 HTTP 测试库（TEST_DB_DSN 门控，与 service 层同策略）：
// 以「真实今天」为中心种三天都开市，断言与运行日解耦。
func marketHTTPDB(t *testing.T) *gorm.DB {
	t.Helper()
	dsn := os.Getenv("TEST_DB_DSN")
	if dsn == "" {
		t.Skip("TEST_DB_DSN 未设置，跳过集成测试")
	}
	if strings.Contains(dsn, "dbname=quant_system") && !strings.Contains(dsn, "dbname=quant_system_test") {
		t.Fatal("拒绝连接生产库 quant_system，请使用独立测试库 quant_system_test")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{})
	if err != nil {
		t.Fatalf("连接测试库失败: %v", err)
	}
	if err := db.Migrator().DropTable(&model.TradeCalendar{}); err != nil {
		t.Fatalf("清理 trade_calendar 失败: %v", err)
	}
	if err := db.AutoMigrate(&model.TradeCalendar{}); err != nil {
		t.Fatalf("AutoMigrate trade_calendar 失败: %v", err)
	}
	// 以今天为中心连续三天开市（今天=交易日，昨天=上一交易日，明天=下一交易日）
	now := time.Now()
	seed := []model.TradeCalendar{
		{CalDate: now.AddDate(0, 0, -1), IsOpen: true},
		{CalDate: now, IsOpen: true},
		{CalDate: now.AddDate(0, 0, 1), IsOpen: true},
	}
	if err := db.Create(&seed).Error; err != nil {
		t.Fatalf("seed 失败: %v", err)
	}
	return db
}

// TestGetMarketStatusHTTP 走真实 HTTP 层验证 GET /api/v1/market/status 的 JSON 契约
// （右上角 chip 数据源；Bug #1 修复的端到端验证）。
func TestGetMarketStatusHTTP(t *testing.T) {
	db := marketHTTPDB(t)
	svc := service.NewMarketStatusService(db)

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/api/v1/market/status", GetMarketStatus(svc))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/market/status", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("GET /market/status = %d, want 200; body=%s", w.Code, w.Body.String())
	}

	var body struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
		Data    struct {
			Today         string `json:"today"`
			IsTradeDay    bool   `json:"is_trade_day"`
			MarketPhase   string `json:"market_phase"`
			PhaseLabel    string `json:"phase_label"`
			LastTradeDate string `json:"last_trade_date"`
			NextTradeDate string `json:"next_trade_date"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("解析响应失败: %v; body=%s", err, w.Body.String())
	}
	if body.Code != response.CodeOK {
		t.Fatalf("code = %d, want %d", body.Code, response.CodeOK)
	}
	now := time.Now().Format("2006-01-02")
	yesterday := time.Now().AddDate(0, 0, -1).Format("2006-01-02")
	if !body.Data.IsTradeDay {
		t.Errorf("is_trade_day = false, want true（seed 已把今天置为开市）")
	}
	if body.Data.Today != now {
		t.Errorf("today = %s, want %s", body.Data.Today, now)
	}
	if body.Data.LastTradeDate != yesterday || body.Data.NextTradeDate != now {
		t.Errorf("last/next = %s/%s, want %s/%s",
			body.Data.LastTradeDate, body.Data.NextTradeDate, yesterday, now)
	}
	switch body.Data.MarketPhase {
	case "pre_open", "open", "lunch_break", "closed":
	default:
		t.Errorf("交易日 market_phase = %q, want 盘中阶段之一（非 off_day）", body.Data.MarketPhase)
	}
	if body.Data.PhaseLabel == "" || body.Data.PhaseLabel == "休市" {
		t.Errorf("phase_label = %q, want 非休市的中文标签", body.Data.PhaseLabel)
	}
}

// quoteDB 指数行情测试库：清空 daily_price 后种两日指数收盘（2026-08-20 收盘 100 / 2026-08-21 收盘 102）
func quoteDB(t *testing.T) *gorm.DB {
	t.Helper()
	dsn := os.Getenv("TEST_DB_DSN")
	if dsn == "" {
		t.Skip("TEST_DB_DSN 未设置，跳过集成测试")
	}
	if strings.Contains(dsn, "dbname=quant_system") && !strings.Contains(dsn, "dbname=quant_system_test") {
		t.Fatal("拒绝连接生产库 quant_system，请使用独立测试库 quant_system_test")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{})
	if err != nil {
		t.Fatalf("连接测试库失败: %v", err)
	}
	if err := db.Migrator().DropTable(&model.DailyPrice{}); err != nil {
		t.Fatalf("清理 daily_price 失败: %v", err)
	}
	if err := db.AutoMigrate(&model.DailyPrice{}); err != nil {
		t.Fatalf("AutoMigrate daily_price 失败: %v", err)
	}
	seed := []model.DailyPrice{
		{Code: "sh000001", TradeDate: time.Date(2026, 8, 20, 0, 0, 0, 0, time.UTC), Close: 100},
		{Code: "sh000001", TradeDate: time.Date(2026, 8, 21, 0, 0, 0, 0, time.UTC), Close: 102},
		{Code: "sh000300", TradeDate: time.Date(2026, 8, 21, 0, 0, 0, 0, time.UTC), Close: 4000},
	}
	if err := db.Create(&seed).Error; err != nil {
		t.Fatalf("seed 失败: %v", err)
	}
	return db
}

// TestGetIndexQuotesHTTP 走真实 HTTP 层验证 GET /index/quotes（topbar 三枚指数芯片；
// 功能建议 ① 上证指数；收盘 + 较上一交易日涨跌幅%）。
func TestGetIndexQuotesHTTP(t *testing.T) {
	db := quoteDB(t)
	repo := repository.NewDailyRepository(db)

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/api/v1/index/quotes", GetIndexQuotes(repo))

	req := httptest.NewRequest(http.MethodGet, "/api/v1/index/quotes?codes=sh000001,sh000300", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("GET /index/quotes = %d, want 200; body=%s", w.Code, w.Body.String())
	}
	var body struct {
		Code int `json:"code"`
		Data struct {
			Items []struct {
				Code      string  `json:"code"`
				Name      string  `json:"name"`
				Close     float64 `json:"close"`
				ChangePct float64 `json:"change_pct"`
				TradeDate string  `json:"trade_date"`
			} `json:"items"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("解析响应失败: %v; body=%s", err, w.Body.String())
	}
	if body.Code != response.CodeOK {
		t.Fatalf("code = %d, want %d", body.Code, response.CodeOK)
	}
	if len(body.Data.Items) != 2 {
		t.Fatalf("items = %d, want 2（sh000001 + sh000300）", len(body.Data.Items))
	}
	sz := body.Data.Items[0]
	if sz.Code != "sh000001" || sz.Name != "上证指数" {
		t.Errorf("items[0] = %+v, want sh000001/上证指数", sz)
	}
	if sz.Close != 102 || sz.ChangePct != 2.0 {
		t.Errorf("sh000001 close/chg = %.2f/%.2f, want 102/2.0（100→102）", sz.Close, sz.ChangePct)
	}
	if sz.TradeDate != "2026-08-21" {
		t.Errorf("trade_date = %s, want 2026-08-21", sz.TradeDate)
	}
	hs := body.Data.Items[1]
	if hs.ChangePct != 0 {
		t.Errorf("sh000300 单日无上一交易日, change_pct = %v, want 0", hs.ChangePct)
	}
}
