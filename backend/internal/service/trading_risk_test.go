package service

import (
	"strings"
	"testing"

	"gorm.io/datatypes"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"quant-system/backend/internal/config"
	"quant-system/backend/internal/model"
)

// Iteration 4 风控测试（§3.4 止损 / 回撤熔断 / 行业集中，Go 执行层口径）。
// 数字口径与 quant-engine/tests/test_risk_mirror.py 的 fixture 一致，两端同参
// 同输入 → 同现金/持仓演化（费率见 broker.go 注释，与 Python Broker 共用）。
//
// 共同前置：active 策略行（params 带风控参数）+ 账户 + 持仓 + 行情。每个用例
// 独立建库（freshDB），不依赖生产数据。

// seedRiskBase 通用前置：扩展表迁移 + position 唯一索引 + active 策略 + 账户
func seedRiskBase(t *testing.T, db *gorm.DB, params string) uint64 {
	// ExecuteDay 还读 strategy / daily_price / stock_basic（GetSignals join / GetByCode）
	if err := db.AutoMigrate(&model.Strategy{}, &model.DailyPrice{}, &model.StockBasic{}); err != nil {
		t.Fatalf("AutoMigrate 扩展表失败: %v", err)
	}
	if err := db.Exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_position_account_code ON position (account_id, code)`).Error; err != nil {
		t.Fatalf("创建 position 唯一索引: %v", err)
	}
	// strategy 表不在 freshDB 清理范围，重复运行会残留旧行 → 按 name UPSERT 覆盖为
	// active + 本用例 params（保证 GetStrategies 读到正确的风控参数）
	if err := db.Clauses(clause.OnConflict{
		Columns:   []clause.Column{{Name: "name"}},
		DoUpdates: clause.AssignmentColumns([]string{"status", "params"}),
	}).Create(&model.Strategy{
		Name: "multi_factor", Status: model.StrategyActive,
		Params: datatypes.JSON([]byte(params)),
	}).Error; err != nil {
		t.Fatalf("seed active strategy: %v", err)
	}
	acc := model.Account{Name: "主账户", Cash: 0, TotalAsset: 0}
	if err := db.Create(&acc).Error; err != nil {
		t.Fatalf("seed account: %v", err)
	}
	return acc.ID
}

func seedRiskBars(t *testing.T, db *gorm.DB, rows []model.DailyPrice) {
	// daily_price 不在 freshDB 清理范围，先删旧行避免串扰（其他用例/上次运行残留）
	codes := make([]string, 0, len(rows))
	for _, bar := range rows {
		codes = append(codes, bar.Code)
	}
	if err := db.Where("code IN (?)", codes).Delete(&model.DailyPrice{}).Error; err != nil {
		t.Fatalf("清理旧 daily_price: %v", err)
	}
	for _, bar := range rows {
		if err := db.Create(&bar).Error; err != nil {
			t.Fatalf("seed daily_price %s/%v: %v", bar.Code, bar.TradeDate, err)
		}
	}
}

func seedRiskStocks(t *testing.T, db *gorm.DB, rows []model.StockBasic) {
	// stock_basic 部署库已有种子行，测试用例需覆盖行业 → 先删旧行再插入
	codes := make([]string, 0, len(rows))
	for _, s := range rows {
		codes = append(codes, s.Code)
	}
	if err := db.Where("code IN (?)", codes).Delete(&model.StockBasic{}).Error; err != nil {
		t.Fatalf("清理旧 stock_basic: %v", err)
	}
	for _, s := range rows {
		if err := db.Create(&s).Error; err != nil {
			t.Fatalf("seed stock_basic %s: %v", s.Code, err)
		}
	}
}

func riskSvc(db *gorm.DB) *TradingService {
	return NewTradingService(db, config.AccountConfig{
		InitialCash: 100000, CommissionRate: 0.00025, MinCommission: 5.0,
		StampTaxRate: 0.0005, Slippage: 0.001,
	})
}

// ---------- 止损（§6 Go #4） ----------

func TestRiskStopLossScan(t *testing.T) {
	db := freshDB(t)
	d := consistencyDay(t, "2026-08-20")
	dPrev := consistencyDay(t, "2026-08-19")
	accID := seedRiskBase(t, db, `{"top_n":10,"max_position_pct":0.20,"stop_loss_pct":0.15}`)

	if err := db.Model(&model.Account{}).Where("id = ?", accID).
		Updates(map[string]interface{}{"cash": 89985.00, "total_asset": 113985.00}).Error; err != nil {
		t.Fatal(err)
	}
	// 持仓 600000 2000@12.00（可用 2000）；D 收 10.00（前收 10.90，非跌停）。
	// 成交价 10.00×0.999=9.99、金额 19980、佣金 5（下限）、印花税 9.99 均为精确
	// 两位小数，与 Python 镜像（test_risk_mirror.py）同 fixture 同现金。
	if err := db.Create(&model.Position{
		AccountID: accID, Code: "600000", Quantity: 2000, AvailableQty: 2000,
		CostPrice: 12.00, CurrentPrice: 12.00, MarketValue: 24000,
	}).Error; err != nil {
		t.Fatalf("seed position: %v", err)
	}
	seedRiskBars(t, db, []model.DailyPrice{
		{Code: "600000", TradeDate: dPrev, Close: 10.90},
		{Code: "600000", TradeDate: d, Close: 10.00},
	})

	res, err := riskSvc(db).ExecuteDay(accID, d)
	if err != nil {
		t.Fatalf("ExecuteDay: %v", err)
	}
	if res.Skipped {
		t.Fatal("fixture 不应触发幂等跳过")
	}
	// 止损触发：profit_rate=(10.00-12.00)/12.00=-16.67% ≤ -15%
	if res.RiskActions != 1 {
		t.Errorf("RiskActions = %d, want 1", res.RiskActions)
	}
	if res.SellCount != 0 || res.BuyCount != 0 {
		t.Errorf("止损不计入 SellCount/BuyCount: SellCount=%d BuyCount=%d", res.SellCount, res.BuyCount)
	}
	if res.Fused {
		t.Error("无熔断参数不应触发熔断")
	}
	// 成交价 9.99（10.00×0.999），金额 19980，佣金 5.00（下限），印花税 9.99 → 回款 19965.01
	var acc model.Account
	if err := db.First(&acc, accID).Error; err != nil {
		t.Fatal(err)
	}
	if acc.Cash != 109950.01 {
		t.Errorf("现金 = %v, want 109950.01", acc.Cash)
	}
	var cnt int64
	if err := db.Model(&model.Position{}).Where("account_id = ? AND code = ?", accID, "600000").
		Count(&cnt).Error; err != nil {
		t.Fatal(err)
	}
	if cnt != 0 {
		t.Error("止损后持仓应清仓")
	}
	// 止损单 reason 前缀「止损」
	var ord model.Order
	if err := db.Where("account_id = ? AND direction = ?", accID, model.ActionSell).
		Order("id desc").First(&ord).Error; err != nil {
		t.Fatalf("应有一笔止损卖出单: %v", err)
	}
	if !strings.Contains(ord.Reason, "止损") {
		t.Errorf("止损单 reason = %q, want 含「止损」", ord.Reason)
	}
	if ord.Status != model.OrderFilled {
		t.Errorf("止损单状态 = %s, want FILLED", ord.Status)
	}
}

// ---------- 回撤熔断（§6 Go #5） ----------

func TestRiskDrawdownFuse(t *testing.T) {
	db := freshDB(t)
	d := consistencyDay(t, "2026-08-20")
	dPrev := consistencyDay(t, "2026-08-19")
	accID := seedRiskBase(t, db, `{"top_n":10,"max_position_pct":0.20,"drawdown_fuse_pct":0.10}`)

	if err := db.Model(&model.Account{}).Where("id = ?", accID).
		Updates(map[string]interface{}{"cash": 30000.00, "total_asset": 32002.00}).Error; err != nil {
		t.Fatal(err)
	}
	// 历史峰值：dPrev 净值 total_asset=100000（回撤基准）
	if err := db.Create(&model.AccountNav{
		AccountID: accID, TradeDate: dPrev, TotalAsset: 100000,
		Cash: 98000, MarketValue: 2002, Nav: 1, DailyReturn: 0, Drawdown: 0,
	}).Error; err != nil {
		t.Fatalf("seed nav peak: %v", err)
	}
	// 持仓 000001 100@20.02；D 收 20.02（平盘）
	if err := db.Create(&model.Position{
		AccountID: accID, Code: "000001", Quantity: 100, AvailableQty: 100,
		CostPrice: 20.02, CurrentPrice: 20.02, MarketValue: 2002,
	}).Error; err != nil {
		t.Fatalf("seed position: %v", err)
	}
	seedRiskBars(t, db, []model.DailyPrice{
		{Code: "000001", TradeDate: dPrev, Close: 20.02},
		{Code: "000001", TradeDate: d, Close: 20.02},
	})
	// 当日信号：BUY 600002（熔断应跳过）、SELL 000001（熔断日 SELL 照常）
	for _, sg := range []model.StrategySignal{
		{StrategyName: "multi_factor", Code: "600002", TradeDate: d, Score: 95, Action: model.ActionBuy, Reason: "测试"},
		{StrategyName: "multi_factor", Code: "000001", TradeDate: d, Score: 10, Action: model.ActionSell, Reason: "测试"},
	} {
		if err := db.Create(&sg).Error; err != nil {
			t.Fatalf("seed signal %s: %v", sg.Code, err)
		}
	}

	res, err := riskSvc(db).ExecuteDay(accID, d)
	if err != nil {
		t.Fatalf("ExecuteDay: %v", err)
	}
	// 回撤 = (32002-100000)/100000 = -68% ≥ 10% → 熔断
	if !res.Fused {
		t.Error("回撤 68% 应触发熔断")
	}
	if res.BuyCount != 0 {
		t.Errorf("熔断日 BuyCount = %d, want 0（BUY 全跳）", res.BuyCount)
	}
	if res.SellCount != 1 {
		t.Errorf("熔断日 SellCount = %d, want 1（SELL 照常）", res.SellCount)
	}
	// SELL 000001 成交价 20.00（20.02×0.999），金额 2000，佣金 5.00，印花税 1.00 → 回款 1994
	var acc model.Account
	if err := db.First(&acc, accID).Error; err != nil {
		t.Fatal(err)
	}
	if acc.Cash != 31994.00 {
		t.Errorf("现金 = %v, want 31994.00", acc.Cash)
	}
	// BUY 被熔断跳过 → 无 600002 成交/委托
	var cnt int64
	if err := db.Model(&model.Order{}).Where("account_id = ? AND code = ?", accID, "600002").
		Count(&cnt).Error; err != nil {
		t.Fatal(err)
	}
	if cnt != 0 {
		t.Error("熔断日不应产生 600002 任何委托")
	}
}

// ---------- 行业集中度（§6 Go #6） ----------

func TestRiskIndustryLimit(t *testing.T) {
	db := freshDB(t)
	d := consistencyDay(t, "2026-08-20")
	dPrev := consistencyDay(t, "2026-08-19")
	accID := seedRiskBase(t, db, `{"top_n":10,"max_position_pct":0.20,"industry_limit_pct":0.10}`)

	if err := db.Model(&model.Account{}).Where("id = ?", accID).
		Updates(map[string]interface{}{"cash": 81972.00, "total_asset": 99995.00}).Error; err != nil {
		t.Fatal(err)
	}
	// 持仓 600000 1000@10.01 + 000001 400@20.02，均属「银行」行业
	for _, p := range []model.Position{
		{AccountID: accID, Code: "600000", Quantity: 1000, AvailableQty: 1000,
			CostPrice: 10.01, CurrentPrice: 10.01, MarketValue: 10010},
		{AccountID: accID, Code: "000001", Quantity: 400, AvailableQty: 400,
			CostPrice: 20.02, CurrentPrice: 20.02, MarketValue: 8008},
	} {
		if err := db.Create(&p).Error; err != nil {
			t.Fatalf("seed position %s: %v", p.Code, err)
		}
	}
	seedRiskStocks(t, db, []model.StockBasic{
		{Code: "600000", Name: "浦发银行", Market: "SH", Industry: "银行"},
		{Code: "000001", Name: "平安银行", Market: "SZ", Industry: "银行"},
	})
	seedRiskBars(t, db, []model.DailyPrice{
		{Code: "600000", TradeDate: dPrev, Close: 9.50},
		{Code: "600000", TradeDate: d, Close: 10.00},
		{Code: "000001", TradeDate: dPrev, Close: 19.00},
		{Code: "000001", TradeDate: d, Close: 20.02},
	})
	// 当日 BUY 600000（银行行业加仓 → 集中度超限应拒单）
	if err := db.Create(&model.StrategySignal{
		StrategyName: "multi_factor", Code: "600000", TradeDate: d,
		Score: 90, Action: model.ActionBuy, Reason: "测试",
	}).Error; err != nil {
		t.Fatalf("seed signal: %v", err)
	}

	res, err := riskSvc(db).ExecuteDay(accID, d)
	if err != nil {
		t.Fatalf("ExecuteDay: %v", err)
	}
	// 加仓后银行行业占比 = (10000+8008+10.01×900)/99980 = 27.0% > 10% → 拒单
	if res.Rejected != 1 {
		t.Errorf("Rejected = %d, want 1（行业集中超限拒单）", res.Rejected)
	}
	if res.BuyCount != 0 {
		t.Errorf("BuyCount = %d, want 0", res.BuyCount)
	}
	var ord model.Order
	if err := db.Where("account_id = ? AND code = ? AND status = ?", accID, "600000", model.OrderRejected).
		Order("id desc").First(&ord).Error; err != nil {
		t.Fatalf("应有行业集中拒单: %v", err)
	}
	if !strings.Contains(ord.Reason, "行业集中超限") {
		t.Errorf("拒单 reason = %q, want 含「行业集中超限」", ord.Reason)
	}
	// 持仓不变（600000 仍 1000 股）
	var pos model.Position
	if err := db.Where("account_id = ? AND code = ?", accID, "600000").First(&pos).Error; err != nil {
		t.Fatalf("持仓应保留: %v", err)
	}
	if pos.Quantity != 1000 {
		t.Errorf("拒单后 600000 数量 = %d, want 1000", pos.Quantity)
	}
}
