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
	"strconv"
	"strings"
	"time"

	"quant-system/backend/internal/config"
	"quant-system/backend/internal/datahub"
	"quant-system/backend/internal/model"
)

// 数据集 id（值 = datahub 数据集 id，用于 DATAHUB_READ_DATASETS 白名单与 HTTP 路径）。
const (
	DsStockBasic         = "stock_basic"
	DsDailyPrice         = "daily_price"
	DsDailyValuation     = "daily_valuation"
	DsFinancialIndicator = "financial_indicator"
	DsLatestTradeDate    = "latest_trade_date"
	DsMarketReady        = "market_ready"
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

// StockBasicFilter 列表过滤条件（对齐 datahub dataset `stock_basic` 参数；不含分页）。
// 空字段 = 该维不过滤。Codes 逗号 IN；Keyword 代码/名称模糊（大小写不敏感）。
type StockBasicFilter struct {
	Codes    []string
	Industry string
	Keyword  string
	Market   string
	Universe string
	Scope    string
	Sort     string // datahub 白名单：code/name/list_date/market/industry（非法→code）
	Order    string // asc/desc（默认 asc）
}

// StockBasicList 按条件取股票列表（服务端过滤 + 排序，**不含分页**）。
// 分页/总数由调用方在结果上做（datahub 不返回 total）：见 repository.GetList。
func (s *Source) StockBasicList(ctx context.Context, f StockBasicFilter) ([]model.StockBasic, error) {
	p := map[string]string{}
	if len(f.Codes) > 0 {
		p["codes"] = strings.Join(f.Codes, ",")
	}
	if f.Industry != "" {
		p["industry"] = f.Industry
	}
	if f.Keyword != "" {
		p["keyword"] = f.Keyword
	}
	if f.Market != "" {
		p["market"] = f.Market
	}
	if f.Universe != "" {
		p["universe"] = f.Universe
	}
	if f.Scope != "" {
		p["scope"] = f.Scope
	}
	if f.Sort != "" {
		p["sort"] = f.Sort
	}
	if f.Order != "" {
		p["order"] = f.Order
	}
	rows, err := datahub.Fetch[stockBasicRow](s.c, ctx, DsStockBasic, p)
	if err != nil {
		return nil, err
	}
	out := make([]model.StockBasic, 0, len(rows))
	for _, r := range rows {
		out = append(out, toStockBasic(r))
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

// DailyLatest 多码「每 code 最近 n 行」行情（可选锚 asof = trade_date ≤ asof）。
// datahub 侧 ROW_NUMBER(PARTITION BY code ORDER BY trade_date DESC) 窗口，**一次往返**；
// 返回按 (code, trade_date ASC) 升序。
//
// n=1 ⇒ 每码最新一日（Tier1「最新一条」读点）；n=2 ⇒ top-2（指数条/涨跌幅 LAG）；
// n=21 ⇒ 21 根窗（20 日涨幅）。**空结果合法**（该 code 无行则不出现）。
func (s *Source) DailyLatest(ctx context.Context, codes []string, n int, asof *time.Time) ([]model.DailyPrice, error) {
	if len(codes) == 0 {
		return nil, nil
	}
	p := map[string]string{"codes": strings.Join(codes, ",")}
	if n > 0 {
		p["last"] = strconv.Itoa(n)
	}
	if asof != nil {
		p["asof"] = asof.Format(dateLayout)
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

// LatestFactor 该股全量最新非空复权因子（`adj_not_null` + `last=1`）。
// datahub 侧先滤 adj_factor IS NOT NULL 再取每码最新一行；无数据返回 (0, false, nil)。
func (s *Source) LatestFactor(ctx context.Context, code string) (float64, bool, error) {
	rows, err := datahub.Fetch[dailyPriceRow](s.c, ctx, DsDailyPrice, map[string]string{
		"codes": code, "adj_not_null": "true", "last": "1"})
	if err != nil {
		return 0, false, err
	}
	if len(rows) == 0 {
		return 0, false, nil
	}
	return f64(rows[0].AdjFactor), true, nil
}

// LatestTradeDate 全市场最新交易日（`latest_trade_date` 数据集 MAX(trade_date)）。
// excludeIndex=true 排除 sh% 指数行（对齐 qe 口径 `code NOT LIKE 'sh%'`）；
// false = 不过滤（对齐 backend GetLatestTradeDate，含指数行）。无数据返回 (nil, nil)。
func (s *Source) LatestTradeDate(ctx context.Context, excludeIndex bool) (*time.Time, error) {
	rows, err := datahub.Fetch[latestTradeDateRow](s.c, ctx, DsLatestTradeDate, map[string]string{
		"table": DsDailyPrice, "exclude_index": strconv.FormatBool(excludeIndex)})
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	d := rows[0].TradeDate.Time
	return &d, nil
}

// ---- daily_valuation ----

// ValuationLast 多码「每 code 最近 n 行」估值（可选锚 asof = trade_date ≤ asof）。
// datahub 侧用 ROW_NUMBER(PARTITION BY code ORDER BY trade_date DESC) 窗口，
// **一次往返、无 N+1**；返回按 (code, trade_date ASC) 升序。
//
// n=1 ⇒ 每码取最新一日（对应 SQL「最新一条」读点）；n>1 ⇒ top-N 窗（供 Go 归约）。
// 注意：模型 Pe/Pb 等为 float64，datahub 的 NULL 经 toValuation 归 0 —— 调用方
// 以「≠0」充当「非空」判定（PE/PB 真实数据恒不恰为 0，无信息损失）。
func (s *Source) ValuationLast(ctx context.Context, codes []string, n int, asof *time.Time) ([]model.DailyValuation, error) {
	if len(codes) == 0 {
		return nil, nil
	}
	p := map[string]string{"codes": strings.Join(codes, ",")}
	if n > 0 {
		p["last"] = strconv.Itoa(n)
	}
	if asof != nil {
		p["asof"] = asof.Format(dateLayout)
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

// FinancialLast 多码「每 code 最近 n 期」财务（可选锚 asOf = announce_date ≤ asOf）。
// datahub 侧 ROW_NUMBER 按 (announce_date DESC, report_date DESC) 取最近 n 期；
// n=1 ⇒ 每码最新一期。返回按 (code, report_date ASC) 升序。
//
// 注：announce_date 为 NOT NULL（schema），无 NULLS FIRST 风险；roe 等 NULL 经
// toFinancial 归 0，调用方以「≠0」充当非空判定（对齐 SQL `IS NOT NULL`）。
func (s *Source) FinancialLast(ctx context.Context, codes []string, asOf *time.Time, n int) ([]model.FinancialIndicator, error) {
	if len(codes) == 0 {
		return nil, nil
	}
	p := map[string]string{"codes": strings.Join(codes, ",")}
	if asOf != nil {
		p["as_of"] = asOf.Format(dateLayout)
	}
	if n > 0 {
		p["last"] = strconv.Itoa(n)
	}
	rows, err := datahub.Fetch[financialRow](s.c, ctx, DsFinancialIndicator, p)
	if err != nil {
		return nil, err
	}
	out := make([]model.FinancialIndicator, 0, len(rows))
	for _, r := range rows {
		out = append(out, toFinancial(r))
	}
	return out, nil
}
