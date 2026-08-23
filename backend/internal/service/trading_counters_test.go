package service

import (
	"testing"

	"gorm.io/datatypes"
	"gorm.io/gorm/clause"

	"quant-system/backend/internal/config"
	"quant-system/backend/internal/model"
)

// TestExecuteDayCounters 回归：res.BuyCount / res.SellCount 定义了却从不递增，
// 导致 task_run 台账"买入 X/卖出 X"恒为 0（即使真实发生了买卖）。
// 修复后：仅在 recordFill 成功处累加；停牌/无持仓等 ("",nil) skip 路径不计。
//
// Fixture（date D，均无涨跌停干扰；无净值快照、无策略单 → 不走幂等跳过）：
//   持仓 000001 400 股（成本 19.00，前收 19.00，D 收 20.00）→ 信号 SELL → SellCount=1
//   600000（前收 9.50，D 收 10.00）                       → 信号 BUY  → BuyCount=1
//
// TEST_DB_DSN 门控：未设置时跳过（本地/CI 设置后自动启用），模式对齐 freshDB。
func TestExecuteDayCounters(t *testing.T) {
	db := freshDB(t)
	d := consistencyDay(t, "2026-08-20")
	dPrev := consistencyDay(t, "2026-08-19")

	// ExecuteDay 还读 strategy / daily_price / stock_basic（GetSignals join），freshDB 未迁移 → 补建表
	if err := db.AutoMigrate(&model.Strategy{}, &model.DailyPrice{}, &model.StockBasic{}); err != nil {
		t.Fatalf("AutoMigrate 扩展表失败: %v", err)
	}
	// savePosition 的 Upsert 走 ON CONFLICT (account_id, code)，AutoMigrate 不建唯一索引
	if err := db.Exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_position_account_code ON position (account_id, code)`).Error; err != nil {
		t.Fatalf("创建 position 唯一索引: %v", err)
	}
	// strategy 表不在 freshDB 清理范围，重复运行会撞唯一键 → 幂等写入
	if err := db.Clauses(clause.OnConflict{DoNothing: true}).Create(&model.Strategy{
		Name:   "multi_factor",
		Params: datatypes.JSON([]byte(`{"top_n":20,"max_position_pct":0.20}`)),
	}).Error; err != nil {
		t.Fatalf("seed strategy: %v", err)
	}

	acc := model.Account{Name: "主账户", Cash: 100000, TotalAsset: 100000}
	if err := db.Create(&acc).Error; err != nil {
		t.Fatalf("seed account: %v", err)
	}
	if err := db.Create(&model.Position{
		AccountID: acc.ID, Code: "000001", Quantity: 400, AvailableQty: 400,
		CostPrice: 19.00, CurrentPrice: 19.00, MarketValue: 7600,
	}).Error; err != nil {
		t.Fatalf("seed position: %v", err)
	}

	for _, bar := range []model.DailyPrice{
		{Code: "600000", TradeDate: dPrev, Close: 9.50},
		{Code: "600000", TradeDate: d, Close: 10.00},
		{Code: "000001", TradeDate: dPrev, Close: 19.00},
		{Code: "000001", TradeDate: d, Close: 20.00},
	} {
		if err := db.Create(&bar).Error; err != nil {
			t.Fatalf("seed daily_price %s/%v: %v", bar.Code, bar.TradeDate, err)
		}
	}

	for _, sg := range []model.StrategySignal{
		{StrategyName: "multi_factor", Code: "600000", TradeDate: d, Score: 90, Action: model.ActionBuy, Reason: "测试"},
		{StrategyName: "multi_factor", Code: "000001", TradeDate: d, Score: 10, Action: model.ActionSell, Reason: "测试"},
	} {
		if err := db.Create(&sg).Error; err != nil {
			t.Fatalf("seed signal %s: %v", sg.Code, err)
		}
	}

	svc := NewTradingService(db, config.AccountConfig{
		InitialCash: 100000, CommissionRate: 0.00025, MinCommission: 5.0,
		StampTaxRate: 0.0005, Slippage: 0.001,
	})
	res, err := svc.ExecuteDay(acc.ID, d)
	if err != nil {
		t.Fatalf("ExecuteDay: %v", err)
	}
	if res.Skipped {
		t.Fatal("fixture 不应触发幂等跳过")
	}
	if res.BuyCount != 1 {
		t.Errorf("BuyCount = %d, want 1", res.BuyCount)
	}
	if res.SellCount != 1 {
		t.Errorf("SellCount = %d, want 1", res.SellCount)
	}
	if res.Rejected != 0 {
		t.Errorf("Rejected = %d, want 0", res.Rejected)
	}

	// 落库侧：应各有一笔 FILLED 委托 + 一笔成交
	var orders int64
	if err := db.Model(&model.Order{}).Where("status = ?", model.OrderFilled).Count(&orders).Error; err != nil {
		t.Fatalf("count filled orders: %v", err)
	}
	if orders != 2 {
		t.Errorf("FILLED order 数 = %d, want 2", orders)
	}
	var trades int64
	if err := db.Model(&model.Trade{}).Count(&trades).Error; err != nil {
		t.Fatalf("count trades: %v", err)
	}
	if trades != 2 {
		t.Errorf("trade 数 = %d, want 2", trades)
	}
}
