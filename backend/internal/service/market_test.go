package service

import (
	"os"
	"strings"
	"testing"
	"time"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"

	"quant-system/backend/internal/model"
)

// marketDB 市场状态测试库（TEST_DB_DSN 门控，复用 strategyDB 的拒绝生产库策略）。
func marketDB(t *testing.T) *gorm.DB {
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
	// 种子：2026-08-21(周五) 开市，08-22/23(周末) 休市，08-24(周一) 开市
	seed := []model.TradeCalendar{
		{CalDate: time.Date(2026, 8, 21, 0, 0, 0, 0, time.UTC), IsOpen: true},
		{CalDate: time.Date(2026, 8, 22, 0, 0, 0, 0, time.UTC), IsOpen: false},
		{CalDate: time.Date(2026, 8, 23, 0, 0, 0, 0, time.UTC), IsOpen: false},
		{CalDate: time.Date(2026, 8, 24, 0, 0, 0, 0, time.UTC), IsOpen: true},
		{CalDate: time.Date(2026, 8, 25, 0, 0, 0, 0, time.UTC), IsOpen: true},
	}
	if err := db.Create(&seed).Error; err != nil {
		t.Fatalf("seed 失败: %v", err)
	}
	return db
}

func TestMarketStatus(t *testing.T) {
	db := marketDB(t)
	svc := NewMarketStatusService(db)
	cst := time.FixedZone("CST", 8*3600)

	// 交易日盘中（08-24 周一 10:00 CST）→ 交易中
	st, err := svc.GetStatus(time.Date(2026, 8, 24, 10, 0, 0, 0, cst))
	mustNoErr(t, err)
	if !st.IsTradeDay || st.MarketPhase != "open" || st.PhaseLabel != "交易中" {
		t.Errorf("周一盘中 = %+v, want is_trade_day=true phase=open 交易中", st)
	}
	if st.LastTradeDate != "2026-08-21" || st.NextTradeDate != "2026-08-24" {
		t.Errorf("交易日 last/next = %s/%s, want 2026-08-21/2026-08-24", st.LastTradeDate, st.NextTradeDate)
	}

	// 交易日盘前（08-24 08:00）→ 未开盘
	st, err = svc.GetStatus(time.Date(2026, 8, 24, 8, 0, 0, 0, cst))
	mustNoErr(t, err)
	if st.MarketPhase != "pre_open" || st.PhaseLabel != "未开盘" {
		t.Errorf("盘前 = %+v, want pre_open 未开盘", st)
	}

	// 周末（08-23 周日）→ 非交易日 休市
	st, err = svc.GetStatus(time.Date(2026, 8, 23, 12, 0, 0, 0, cst))
	mustNoErr(t, err)
	if st.IsTradeDay || st.MarketPhase != "off_day" || st.PhaseLabel != "休市" {
		t.Errorf("周日 = %+v, want is_trade_day=false off_day 休市", st)
	}
	if st.LastTradeDate != "2026-08-21" || st.NextTradeDate != "2026-08-24" {
		t.Errorf("周末 last/next = %s/%s, want 2026-08-21/2026-08-24", st.LastTradeDate, st.NextTradeDate)
	}
}
