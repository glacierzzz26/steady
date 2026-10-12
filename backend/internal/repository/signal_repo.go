package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"log"
	"sort"
	"time"

	"gorm.io/gorm"

	"quant-system/backend/internal/datasource"
	"quant-system/backend/internal/model"
)

// SignalRepository 策略与信号数据访问层（只读）
//
// ds（datahub 读取源）为可选：nil ⇒ 恒走本地 gorm（含全部 tx-scoped 构造）。
// 非 nil 且 daily_valuation 闸门开 ⇒ GetSignalPe 走 datahub，其余读点仍本地。
type SignalRepository struct {
	db *gorm.DB
	ds *datasource.Source
}

// NewSignalRepository 构造。ds 变参可选：现有 `NewSignalRepository(db)` / tx-scoped
// `NewSignalRepository(tx)` 调用点零改动（不传 ⇒ nil ⇒ 纯本地，保事务内一致）。
func NewSignalRepository(db *gorm.DB, ds ...*datasource.Source) *SignalRepository {
	r := &SignalRepository{db: db}
	if len(ds) > 0 {
		r.ds = ds[0]
	}
	return r
}

// useDS daily_valuation 是否走 datahub（闸门判定）。
func (r *SignalRepository) useDS() bool {
	return r.ds != nil && r.ds.Enabled(datasource.DsDailyValuation)
}

// dsFallback 读失败处置：开 fallback ⇒ 记 WARNING 并回退本地（返回 true）；
// 否则失败即抛（返回 false，不静默给陈旧值）。
func (r *SignalRepository) dsFallback(err error) bool {
	if r.ds.FallbackLocal() {
		log.Printf("WARN datahub daily_valuation 读失败，回退本地: %v", err)
		return true
	}
	return false
}

// SignalItem 信号 + 股票名称（join stock_basic）+ G1 扩展字段
// Rank/分项/pe/chg20 无数据时为 nil（前端空态「—」，不造假）
type SignalItem struct {
	Code    string
	Name    string
	Score   float64
	Action  string
	Reason  string
	Rank    *int      // 横截面排名（当日该策略全部信号按 score 降序 RANK，同 reason「排名 X/N」）
	Trend   *float64  // 趋势分项（Σ weight×normalized ×100）
	Value   *float64  // 价值分项
	Quality *float64  // 质量分项
	Risk    *float64  // 风险分项
	Pe      *float64  // PE(TTM)（daily_valuation 最新）
	Chg20   *float64  // 前复权 20 日涨幅（百分比数值，如 15.2 = 15.2%）
}

// GetStrategies 活跃策略列表
func (r *SignalRepository) GetStrategies() ([]model.Strategy, error) {
	var items []model.Strategy
	err := r.db.Where("status = ?", "active").Find(&items).Error
	return items, err
}

// GetLatestSignalDate 策略最近一次信号日期；无信号返回 (nil, nil)
// 注意：MAX 在空表上返回 NULL，需 Scan 进 sql.NullTime（time.Time 无法接收 NULL）
func (r *SignalRepository) GetLatestSignalDate(strategy string) (*time.Time, error) {
	var t sql.NullTime
	err := r.db.Table("strategy_signal").
		Select("MAX(trade_date)").
		Where("strategy_name = ?", strategy).
		Scan(&t).Error
	if err != nil {
		return nil, err
	}
	if !t.Valid {
		return nil, nil
	}
	return &t.Time, nil
}

// GetSignals 指定策略+日期的信号（评分降序；date 零值表示不限定；action 可选过滤）
func (r *SignalRepository) GetSignals(strategy string, date time.Time,
	action string, limit int) ([]SignalItem, error) {
	items, _, err := r.getSignals(strategy, date, action, 1, limit)
	return items, err
}

// GetSignalsPage 信号分页（评分降序），返回 (items, total)
func (r *SignalRepository) GetSignalsPage(strategy string, date time.Time,
	action string, page, pageSize int) ([]SignalItem, int64, error) {
	return r.getSignals(strategy, date, action, page, pageSize)
}

func (r *SignalRepository) getSignals(strategy string, date time.Time,
	action string, page, pageSize int) ([]SignalItem, int64, error) {
	// 过滤条件独立建 query：Count 与数据查询共用
	filter := func(q *gorm.DB) *gorm.DB {
		q = q.Where("strategy_signal.strategy_name = ?", strategy)
		if !date.IsZero() {
			q = q.Where("strategy_signal.trade_date = ?", date)
		}
		if action != "" {
			q = q.Where("strategy_signal.action = ?", action)
		}
		return q
	}
	var total int64
	if err := filter(r.db.Table("strategy_signal")).Count(&total).Error; err != nil {
		return nil, 0, err
	}
	var items []SignalItem
	err := filter(r.db.Table("strategy_signal")).
		Select("strategy_signal.code, stock_basic.name, strategy_signal.score, "+
			"strategy_signal.action, strategy_signal.reason").
		Joins("LEFT JOIN stock_basic ON stock_basic.code = strategy_signal.code").
		Order("strategy_signal.score DESC").
		Offset((page - 1) * pageSize).Limit(pageSize).
		Scan(&items).Error
	if err != nil {
		return nil, total, err
	}
	// G1：批量补充 rank/四因子分项/pe/chg20（分页后仅补充本页，避免全表富集）
	if !date.IsZero() {
		if err := r.enrichSignals(items, strategy, date); err != nil {
			return nil, total, err
		}
	}
	return items, total, nil
}

// ---- G1/G3 信号扩展字段富集 ----

// enrichSignals 为一批信号补充 G1 字段（rank / 四因子分项 / pe / chg20）
func (r *SignalRepository) enrichSignals(items []SignalItem, strategy string, date time.Time) error {
	codes := make([]string, len(items))
	for i, it := range items {
		codes[i] = it.Code
	}
	ranks, err := r.GetSignalRanks(strategy, date)
	if err != nil {
		return err
	}
	subs, err := r.GetSignalSubscores(codes, date, strategy)
	if err != nil {
		return err
	}
	pes, err := r.GetSignalPe(codes, date)
	if err != nil {
		return err
	}
	chgs, err := r.GetSignalChg20(codes, date)
	if err != nil {
		return err
	}
	for i := range items {
		if v, ok := ranks[items[i].Code]; ok {
			items[i].Rank = &v
		}
		if m, ok := subs[items[i].Code]; ok {
			if v, ok := m["trend"]; ok {
				items[i].Trend = &v
			}
			if v, ok := m["value"]; ok {
				items[i].Value = &v
			}
			if v, ok := m["quality"]; ok {
				items[i].Quality = &v
			}
			if v, ok := m["risk"]; ok {
				items[i].Risk = &v
			}
		}
		if v, ok := pes[items[i].Code]; ok {
			items[i].Pe = &v
		}
		if v, ok := chgs[items[i].Code]; ok {
			items[i].Chg20 = &v
		}
	}
	return nil
}

// GetSignalRanks 横截面排名：某策略某日全部信号按原始分降序 RANK
// 与 reason「排名 X/735」同口径 —— 引擎对 100×Σ(weight×normalized) 未舍入分
// rank(method="min")，strategy_signal.score 仅存 2 位小数，直接对 score 排名会在
// 舍入并列区产生 ±1 偏差，故由 factor_value + 策略因子级权重在 Go 侧重算原始分
// （Iteration 4：权重口径从 factor_definition 切到策略自带 factor_weights）
func (r *SignalRepository) GetSignalRanks(strategy string, date time.Time) (map[string]int, error) {
	weights, err := r.strategyFactorWeights(strategy)
	if err != nil {
		return nil, err
	}
	type row struct {
		Code    string
		Factor  string
		Normal  float64
	}
	var rows []row
	err = r.db.Table("factor_value").
		Select("code AS code, factor_name AS factor, normalized AS normal").
		Where("trade_date = ?", date).
		Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	raw := make(map[string]float64)
	for _, rr := range rows {
		w, ok := weights[rr.Factor]
		if !ok {
			continue
		}
		raw[rr.Code] += w * rr.Normal
	}
	ranks := rankRawScores(raw)

	// 仅保留该策略当日实际有信号的 code
	var codes []string
	if err := r.db.Table("strategy_signal").
		Where("strategy_name = ? AND trade_date = ?", strategy, date).
		Pluck("code", &codes).Error; err != nil {
		return nil, err
	}
	out := make(map[string]int, len(codes))
	for _, c := range codes {
		if v, ok := ranks[c]; ok {
			out[c] = v
		}
	}
	return out, nil
}

// GetSignalSubscores 四因子分项分：Σ(weight × normalized) × 100 per category
// weight 取策略因子级权重（缺失回退 factor_definition）；category 仍取 factor_definition
// —— 与引擎 multi_factor.py 同源（Iteration 4 权重口径切换）
func (r *SignalRepository) GetSignalSubscores(codes []string, date time.Time, strategy string) (map[string]map[string]float64, error) {
	if len(codes) == 0 {
		return map[string]map[string]float64{}, nil
	}
	weights, err := r.strategyFactorWeights(strategy)
	if err != nil {
		return nil, err
	}
	cats, err := r.factorCategories()
	if err != nil {
		return nil, err
	}
	type row struct {
		Code    string
		Factor  string
		Normal  float64
	}
	var rows []row
	err = r.db.Table("factor_value").
		Select("code AS code, factor_name AS factor, normalized AS normal").
		Where("trade_date = ? AND code IN (?)", date, codes).
		Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	out := make(map[string]map[string]float64, len(rows))
	for _, rr := range rows {
		w, ok := weights[rr.Factor]
		if !ok {
			continue
		}
		cat, ok := cats[rr.Factor]
		if !ok {
			continue
		}
		if out[rr.Code] == nil {
			out[rr.Code] = make(map[string]float64)
		}
		out[rr.Code][cat] += w * rr.Normal * 100
	}
	return out, nil
}

// strategyFactorWeights 读取策略自带因子级权重（strategy.factor_weights），
// 缺失因子回退 factor_definition.weight（Iteration 4 权重口径）。
// 策略不存在时整体回退 factor_definition（老库未配置策略的场景）。
func (r *SignalRepository) strategyFactorWeights(strategy string) (map[string]float64, error) {
	// 1. factor_definition 全量作为回退基
	var defs []model.FactorDefinition
	if err := r.db.Find(&defs).Error; err != nil {
		return nil, err
	}
	weights := make(map[string]float64, len(defs))
	for _, d := range defs {
		weights[d.Name] = d.Weight
	}
	// 2. 策略自带因子级权重覆盖
	var st model.Strategy
	err := r.db.Where("name = ?", strategy).First(&st).Error
	if err == gorm.ErrRecordNotFound {
		return weights, nil
	}
	if err != nil {
		return nil, err
	}
	if len(st.FactorWeights) > 0 {
		var m map[string]float64
		if err := json.Unmarshal(st.FactorWeights, &m); err != nil {
			return nil, err
		}
		for k, v := range m {
			weights[k] = v
		}
	}
	return weights, nil
}

// factorCategories 因子类别映射（category 是因子级属性，不随策略变）
func (r *SignalRepository) factorCategories() (map[string]string, error) {
	var defs []model.FactorDefinition
	if err := r.db.Find(&defs).Error; err != nil {
		return nil, err
	}
	out := make(map[string]string, len(defs))
	for _, d := range defs {
		out[d.Name] = d.Category
	}
	return out, nil
}

// rankRawScores 原始分 → 排名（RANK，method="min"：并列同分同排名，后续跳位）
// 与引擎 rank(method="min") 同口径。
func rankRawScores(raw map[string]float64) map[string]int {
	if len(raw) == 0 {
		return map[string]int{}
	}
	scores := make([]float64, 0, len(raw))
	for _, v := range raw {
		scores = append(scores, v)
	}
	sort.Sort(sort.Reverse(sort.Float64Slice(scores))) // 降序
	// 去重降序分数 → 排名（并列同分同排名）
	ranks := make(map[string]int, len(raw))
	unique := make([]float64, 0, len(scores))
	for _, s := range scores {
		if len(unique) == 0 || s != unique[len(unique)-1] {
			unique = append(unique, s)
		}
	}
	// 分数种类少（<= 股票数），线性查表
	for code, v := range raw {
		rk := 1
		for i, u := range unique {
			if v == u {
				rk = i + 1
				break
			}
		}
		ranks[code] = rk
	}
	return ranks
}

// GetSignalPe 个股最新 PE(TTM)：取 trade_date <= 信号日 的最新一条
func (r *SignalRepository) GetSignalPe(codes []string, date time.Time) (map[string]float64, error) {
	if len(codes) == 0 {
		return map[string]float64{}, nil
	}
	if r.useDS() {
		// last=1 + asof：每码取 trade_date ≤ date 的最新一行（服务端 ROW_NUMBER），
		// 与原 SQL「JOIN MAX(trade_date)」逐行等价。pe_ttm NULL → 模型 0 → 滤除，
		// 与原 SQL `pe_ttm IS NOT NULL` 同判（PE 真实数据不恰为 0）。
		rows, err := r.ds.ValuationLast(context.Background(), codes, 1, &date)
		if err == nil {
			out := make(map[string]float64, len(rows))
			for _, rr := range rows {
				if rr.PeTtm != 0 {
					out[rr.Code] = rr.PeTtm
				}
			}
			return out, nil
		}
		if !r.dsFallback(err) {
			return nil, err
		}
	}
	type row struct {
		Code string
		Pe   float64
	}
	var rows []row
	err := r.db.Raw(`
		SELECT dv.code, dv.pe_ttm AS pe
		FROM daily_valuation dv
		JOIN (
			SELECT code, MAX(trade_date) AS td
			FROM daily_valuation
			WHERE code IN (?) AND trade_date <= ?
			GROUP BY code
		) latest ON latest.code = dv.code AND latest.td = dv.trade_date
		WHERE dv.pe_ttm IS NOT NULL`, codes, date).Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	out := make(map[string]float64, len(rows))
	for _, rr := range rows {
		out[rr.Code] = rr.Pe
	}
	return out, nil
}

// GetSignalChg20 前复权 20 日涨幅（百分比数值）：
// qfq = close × COALESCE(adj_factor,1)；chg20 = (qfq(rn=1)/qfq(rn=21) − 1) × 100，需 21 根 bar
func (r *SignalRepository) GetSignalChg20(codes []string, date time.Time) (map[string]float64, error) {
	if len(codes) == 0 {
		return map[string]float64{}, nil
	}
	if r.ds != nil && r.ds.Enabled(datasource.DsDailyPrice) {
		// asof=date + last=21：每码 trade_date ≤ date 的最近 21 行（升序）。需满 21 行方可
		// 计算（对齐原 SQL JOIN rn=21：不足 21 行的 code 无行）。qfq = close × COALESCE(adj,1)。
		bars, err := r.ds.DailyLatest(context.Background(), codes, 21, &date)
		if err == nil {
			byCode := make(map[string][]model.DailyPrice, len(bars))
			for _, rr := range bars {
				byCode[rr.Code] = append(byCode[rr.Code], rr)
			}
			out := make(map[string]float64, len(byCode))
			for code, bs := range byCode {
				if len(bs) < 21 {
					continue
				}
				cur := bs[len(bs)-1]
				past := bs[len(bs)-21]
				cq := cur.Close * adjOrOne(cur.AdjFactor)
				pq := past.Close * adjOrOne(past.AdjFactor)
				if pq > 0 {
					out[code] = (cq/pq - 1) * 100
				}
			}
			return out, nil
		}
		if !r.dsFallback(err) {
			return nil, err
		}
	}
	type row struct {
		Code  string
		Chg20 float64
	}
	var rows []row
	err := r.db.Raw(`
		WITH bars AS (
			SELECT code,
				close * COALESCE(adj_factor, 1) AS qfq,
				ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
			FROM daily_price
			WHERE code IN (?) AND trade_date <= ?
		)
		SELECT cur.code, (cur.qfq / past.qfq - 1) * 100 AS chg20
		FROM bars cur
		JOIN bars past ON past.code = cur.code AND past.rn = 21
		WHERE cur.rn = 1 AND past.qfq > 0`, codes, date).Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	out := make(map[string]float64, len(rows))
	for _, rr := range rows {
		out[rr.Code] = rr.Chg20
	}
	return out, nil
}

// GetSignalsRankByDates 历史信号在各自交易日的横截面排名（key: YYYY-MM-DD）
// 排名按 (strategy_name, trade_date) 分区，与 reason「排名 X/N」同口径
func (r *SignalRepository) GetSignalsRankByDates(code string, dates []time.Time) (map[string]int, error) {
	out := make(map[string]int, len(dates))
	if len(dates) == 0 {
		return out, nil
	}
	type row struct {
		TradeDate time.Time
		Rank      int
	}
	var rows []row
	// 注意：窗口函数在 WHERE 之后计算，必须先对全截面排名、再按 code 过滤，否则 rank 恒为 1；
	// 排名用原始分（见 GetSignalRanks 注释），与 reason「排名 X/N」一致
	err := r.db.Raw(`
		SELECT x.code, x.trade_date, x.rank FROM (
			SELECT s.code, s.trade_date,
				RANK() OVER (PARTITION BY s.strategy_name, s.trade_date ORDER BY r.raw_score DESC) AS rank
			FROM strategy_signal s
			JOIN (
				SELECT fv.code, fv.trade_date, 100 * SUM(fd.weight * fv.normalized) AS raw_score
				FROM factor_value fv
				JOIN factor_definition fd ON fd.name = fv.factor_name
				WHERE fv.trade_date IN (?)
				GROUP BY fv.code, fv.trade_date
			) r ON r.code = s.code AND r.trade_date = s.trade_date
			WHERE s.trade_date IN (?)
		) x WHERE x.code = ?`, dates, dates, code).Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	for _, rr := range rows {
		out[rr.TradeDate.Format("2006-01-02")] = rr.Rank
	}
	return out, nil
}

// SignalBrief 信号摘要（G2 股票池列表用）
type SignalBrief struct {
	Score  float64
	Action string
}

// GetSignalsByDateAndCodes 指定日期 + 代码集合的信号（score/action），key: code
func (r *SignalRepository) GetSignalsByDateAndCodes(codes []string, date time.Time) (map[string]SignalBrief, error) {
	out := make(map[string]SignalBrief, len(codes))
	if len(codes) == 0 {
		return out, nil
	}
	type row struct {
		Code   string
		Score  float64
		Action string
	}
	var rows []row
	err := r.db.Table("strategy_signal").
		Select("code, score, action").
		Where("trade_date = ? AND code IN (?)", date, codes).
		Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	for _, rr := range rows {
		out[rr.Code] = SignalBrief{Score: rr.Score, Action: rr.Action}
	}
	return out, nil
}

// StockFactorScore 个股最新因子得分（G3：/stocks/:code factor_score）
type StockFactorScore struct {
	Score   float64
	Rank    *int
	Signal  string
	Trend   *float64
	Value   *float64
	Quality *float64
	Risk    *float64
}

// GetStockFactorScore 个股最新信号 + 排名 + 四因子分项；无信号返回 (nil, nil)
func (r *SignalRepository) GetStockFactorScore(code string) (*StockFactorScore, error) {
	type sigRow struct {
		StrategyName string
		TradeDate    time.Time
		Score        float64
		Action       string
	}
	var sig sigRow
	err := r.db.Table("strategy_signal").
		Select("strategy_name, trade_date, score, action").
		Where("code = ?", code).
		Order("trade_date DESC, id DESC").
		First(&sig).Error
	if err == gorm.ErrRecordNotFound {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	// 排名：该策略该日全部信号按原始分降序 RANK（复用 GetSignalRanks：Go 侧按策略权重计算）
	ranks, err := r.GetSignalRanks(sig.StrategyName, sig.TradeDate)
	if err != nil {
		return nil, err
	}
	// 四因子分项（取最新信号当日，与 G1 同源）
	subs, err := r.GetSignalSubscores([]string{code}, sig.TradeDate, sig.StrategyName)
	if err != nil {
		return nil, err
	}
	fs := &StockFactorScore{Score: sig.Score, Signal: sig.Action}
	if v, ok := ranks[code]; ok {
		fs.Rank = &v
	}
	if m, ok := subs[code]; ok {
		if v, ok := m["trend"]; ok {
			fs.Trend = &v
		}
		if v, ok := m["value"]; ok {
			fs.Value = &v
		}
		if v, ok := m["quality"]; ok {
			fs.Quality = &v
		}
		if v, ok := m["risk"]; ok {
			fs.Risk = &v
		}
	}
	return fs, nil
}

// GetSignalsByCode 个股信号历史（按日期倒序，limit 条）
func (r *SignalRepository) GetSignalsByCode(code string, limit int) ([]model.StrategySignal, error) {
	var items []model.StrategySignal
	err := r.db.Where("code = ?", code).
		Order("trade_date DESC, id DESC").
		Limit(limit).
		Find(&items).Error
	return items, err
}
