// Package datasource 是 Phase 3 读切换的**唯一切换点**（Go 侧）。
//
// 语义对齐 quant-engine `app/data_source.py`：
// - **闸门关**（默认）→ 调用方（repository）保留原 gorm 查询，逐字一致 → 零行为变更。
// - **闸门开** → 本层经 datahub HTTP 取数并转 domain 类型。
//
// 本层**不持有 gorm**：闸门关的本地 SQL 由 repository 保留（避免与 repository 成环、
// 且事务内读必须走 tx，本层无法感知）。切换点 = repository 方法内的 prologue。
package datasource

import (
	"context"
	"strings"
	"time"

	"quant-system/backend/internal/config"
	"quant-system/backend/internal/datahub"
	"quant-system/backend/internal/model"
)

// 数据集 id（值 = datahub 数据集 id，用于 DATAHUB_READ_DATASETS 白名单与 HTTP 路径）。
const (
	DsStockBasic         = "stock_basic"
	DsTradeCalendar      = "trade_calendar"
	DsDailyPrice         = "daily_price"
	DsDailyValuation     = "daily_valuation"
	DsFinancialIndicator = "financial_indicator"
)

// Source datahub 读取源：闸门判定 + HTTP 取数。并发安全（内含 Client）。
type Source struct {
	cfg config.DatahubConfig
	c   *datahub.Client
}

// New 构造读取源（唯一实例，注入到各 repository）。
func New(cfg config.DatahubConfig) *Source {
	return &Source{cfg: cfg, c: datahub.New(cfg)}
}

// Enabled 该数据集是否走 datahub（闸门判定）。
func (s *Source) Enabled(dataset string) bool { return s.cfg.ReadEnabled(dataset) }

// FallbackLocal 读失败时是否回退本地（默认 false = 失败即抛）。
func (s *Source) FallbackLocal() bool { return s.cfg.FallbackLocal }

// Reset 清客户端 TTL 缓存（测试用）。
func (s *Source) Reset() { s.c.Reset() }

const dateLayout = "2006-01-02"

// ---- stock_basic ----

// StockBasicByCodes 按 code 批量取股票基础信息。
func (s *Source) StockBasicByCodes(ctx context.Context, codes []string) ([]model.StockBasic, error) {
	if len(codes) == 0 {
		return nil, nil
	}
	rows, err := datahub.Fetch[stockBasicRow](s.c, ctx, DsStockBasic,
		map[string]string{"codes": strings.Join(codes, ",")})
	if err != nil {
		return nil, err
	}
	out := make([]model.StockBasic, 0, len(rows))
	for _, r := range rows {
		out = append(out, toStockBasic(r))
	}
	return out, nil
}

// StockBasicByCode 单码；不存在返回 (nil, nil)。
func (s *Source) StockBasicByCode(ctx context.Context, code string) (*model.StockBasic, error) {
	rows, err := s.StockBasicByCodes(ctx, []string{code})
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &rows[0], nil
}

// ---- trade_calendar ----

// OpenDates [start, end] 区间内的交易日（升序）。
func (s *Source) OpenDates(ctx context.Context, start, end time.Time) ([]time.Time, error) {
	rows, err := datahub.Fetch[calendarRow](s.c, ctx, DsTradeCalendar, map[string]string{
		"start": start.Format(dateLayout), "end": end.Format(dateLayout), "is_open": "true"})
	if err != nil {
		return nil, err
	}
	out := make([]time.Time, 0, len(rows))
	for _, r := range rows {
		out = append(out, r.CalDate.Time)
	}
	return out, nil
}

// ---- daily_price ----

// DailyRange 多码区间行情（升序由 datahub 保证）。
func (s *Source) DailyRange(ctx context.Context, codes []string, start, end *time.Time) ([]model.DailyPrice, error) {
	if len(codes) == 0 {
		return nil, nil
	}
	p := map[string]string{"codes": strings.Join(codes, ",")}
	if start != nil {
		p["start"] = start.Format(dateLayout)
	}
	if end != nil {
		p["end"] = end.Format(dateLayout)
	}
	rows, err := datahub.Fetch[dailyPriceRow](s.c, ctx, DsDailyPrice, p)
	if err != nil {
		return nil, err
	}
	out := make([]model.DailyPrice, 0, len(rows))
	for _, r := range rows {
		out = append(out, toDailyPrice(r))
	}
	return out, nil
}

// ---- daily_valuation ----

// ValuationRange 多码区间估值。
func (s *Source) ValuationRange(ctx context.Context, codes []string, start, end *time.Time) ([]model.DailyValuation, error) {
	if len(codes) == 0 {
		return nil, nil
	}
	p := map[string]string{"codes": strings.Join(codes, ",")}
	if start != nil {
		p["start"] = start.Format(dateLayout)
	}
	if end != nil {
		p["end"] = end.Format(dateLayout)
	}
	rows, err := datahub.Fetch[valuationRow](s.c, ctx, DsDailyValuation, p)
	if err != nil {
		return nil, err
	}
	out := make([]model.DailyValuation, 0, len(rows))
	for _, r := range rows {
		out = append(out, toValuation(r))
	}
	return out, nil
}

// ---- financial_indicator ----

// FinancialByCodes 多码财务（防未来函数由调用方按 announce_date 处理）。
func (s *Source) FinancialByCodes(ctx context.Context, codes []string) ([]model.FinancialIndicator, error) {
	if len(codes) == 0 {
		return nil, nil
	}
	rows, err := datahub.Fetch[financialRow](s.c, ctx, DsFinancialIndicator,
		map[string]string{"codes": strings.Join(codes, ",")})
	if err != nil {
		return nil, err
	}
	out := make([]model.FinancialIndicator, 0, len(rows))
	for _, r := range rows {
		out = append(out, toFinancial(r))
	}
	return out, nil
}
