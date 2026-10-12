package repository

import (
	"context"
	"log"
	"time"

	"gorm.io/gorm"

	"quant-system/backend/internal/datasource"
	"quant-system/backend/internal/model"
)

// DailyRepository 日行情数据访问层（只读）
//
// ds（datahub 读取源）为可选：nil ⇒ 恒走本地 gorm（含全部 tx-scoped 构造）。
// 非 nil 且对应数据集闸门开 ⇒ 命中读点走 datahub HTTP，其余读点仍本地。
type DailyRepository struct {
	db *gorm.DB
	ds *datasource.Source
}

// NewDailyRepository 构造。ds 变参可选：现有 `NewDailyRepository(db)` / tx-scoped
// `NewDailyRepository(tx)` 调用点零改动（不传 ⇒ nil ⇒ 纯本地，保事务内一致）。
func NewDailyRepository(db *gorm.DB, ds ...*datasource.Source) *DailyRepository {
	r := &DailyRepository{db: db}
	if len(ds) > 0 {
		r.ds = ds[0]
	}
	return r
}

// useDS 指定数据集是否走 datahub（闸门判定）。daily_price / daily_valuation 分属
// 不同白名单项，故按数据集判定。
func (r *DailyRepository) useDS(dataset string) bool {
	return r.ds != nil && r.ds.Enabled(dataset)
}

// dsFallback 读失败处置：开 fallback ⇒ 记 WARNING 并回退本地（返回 true）；
// 否则失败即抛（返回 false，不静默给陈旧值）。
func (r *DailyRepository) dsFallback(dataset string, err error) bool {
	if r.ds.FallbackLocal() {
		log.Printf("WARN datahub %s 读失败，回退本地: %v", dataset, err)
		return true
	}
	return false
}

// GetRange 区间行情，start/end 为 nil 时不过滤，按 trade_date 升序
func (r *DailyRepository) GetRange(code string, start, end *time.Time) ([]model.DailyPrice, error) {
	if r.useDS(datasource.DsDailyPrice) {
		bars, err := r.ds.DailyRange(context.Background(), []string{code}, start, end)
		if err == nil {
			return bars, nil
		}
		if !r.dsFallback(datasource.DsDailyPrice, err) {
			return nil, err
		}
	}
	var bars []model.DailyPrice
	q := r.db.Where("code = ?", code)
	if start != nil {
		q = q.Where("trade_date >= ?", *start)
	}
	if end != nil {
		q = q.Where("trade_date <= ?", *end)
	}
	err := q.Order("trade_date ASC").Find(&bars).Error
	return bars, err
}

// GetLatest 最近一个交易日的行情，无数据返回 (nil, nil)
func (r *DailyRepository) GetLatest(code string) (*model.DailyPrice, error) {
	if r.useDS(datasource.DsDailyPrice) {
		rows, err := r.ds.DailyLatest(context.Background(), []string{code}, 1, nil)
		if err == nil {
			if len(rows) == 0 {
				return nil, nil
			}
			b := rows[len(rows)-1] // last=1 → 每码恰一行（升序末行即最新）
			return &b, nil
		}
		if !r.dsFallback(datasource.DsDailyPrice, err) {
			return nil, err
		}
	}
	var bar model.DailyPrice
	err := r.db.Where("code = ?", code).Order("trade_date DESC").First(&bar).Error
	if err == gorm.ErrRecordNotFound {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &bar, nil
}

// GetLatestValuation 最近一日估值（详情页日度 PE/PB），无数据返回 (nil, nil)
func (r *DailyRepository) GetLatestValuation(code string) (*model.DailyValuation, error) {
	if r.useDS(datasource.DsDailyValuation) {
		rows, err := r.ds.ValuationLast(context.Background(), []string{code}, 1, nil)
		if err == nil {
			if len(rows) == 0 {
				return nil, nil
			}
			v := rows[len(rows)-1] // last=1 → 每码恰一行（升序末行即最新）
			return &v, nil
		}
		if !r.dsFallback(datasource.DsDailyValuation, err) {
			return nil, err
		}
	}
	var v model.DailyValuation
	err := r.db.Where("code = ?", code).Order("trade_date DESC").First(&v).Error
	if err == gorm.ErrRecordNotFound {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &v, nil
}

// GetByDate 指定交易日行情；当日停牌/无数据返回 (nil, nil)
func (r *DailyRepository) GetByDate(code string, tradeDate time.Time) (*model.DailyPrice, error) {
	var bar model.DailyPrice
	err := r.db.Where("code = ? AND trade_date = ?", code, tradeDate).First(&bar).Error
	if err == gorm.ErrRecordNotFound {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &bar, nil
}

// GetPrevClose 前一交易日收盘价（涨跌停判断），无数据返回 (0, false, nil)
func (r *DailyRepository) GetPrevClose(code string, tradeDate time.Time) (float64, bool, error) {
	var bar model.DailyPrice
	err := r.db.Select("close").
		Where("code = ? AND trade_date < ?", code, tradeDate).
		Order("trade_date DESC").
		First(&bar).Error
	if err == gorm.ErrRecordNotFound {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, err
	}
	return bar.Close, true, nil
}

// GetLatestTradeDate 全市场最近交易日（自动下单日期基准）；无数据返回 (nil, nil)
func (r *DailyRepository) GetLatestTradeDate() (*time.Time, error) {
	if r.useDS(datasource.DsDailyPrice) {
		td, err := r.ds.LatestTradeDate(context.Background(), false) // 不过滤指数行（对齐本地口径）
		if err == nil {
			return td, nil
		}
		if !r.dsFallback(datasource.DsDailyPrice, err) {
			return nil, err
		}
	}
	var bar model.DailyPrice
	err := r.db.Order("trade_date DESC").First(&bar).Error
	if err == gorm.ErrRecordNotFound {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &bar.TradeDate, nil
}

// GetLatestFactor 该股全量最新非空复权因子（前复权锚点，跨查询区间），无数据返回 (0, false, nil)
func (r *DailyRepository) GetLatestFactor(code string) (float64, bool, error) {
	if r.useDS(datasource.DsDailyPrice) {
		v, ok, err := r.ds.LatestFactor(context.Background(), code)
		if err == nil {
			return v, ok, nil
		}
		if !r.dsFallback(datasource.DsDailyPrice, err) {
			return 0, false, err
		}
	}
	var bar model.DailyPrice
	err := r.db.Select("adj_factor").
		Where("code = ? AND adj_factor IS NOT NULL", code).
		Order("trade_date DESC").
		First(&bar).Error
	if err == gorm.ErrRecordNotFound {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, err
	}
	return bar.AdjFactor, true, nil
}

// GetIndexQuotes 指数最新报价（每个 code 最近两个交易日，供收盘价 + 较上一交易日涨跌幅%），
// 按 (code, trade_date ASC) 返回；某指数无数据则该 code 无行。codes 为空返回 nil。
func (r *DailyRepository) GetIndexQuotes(codes []string) ([]model.DailyPrice, error) {
	if len(codes) == 0 {
		return nil, nil
	}
	if r.useDS(datasource.DsDailyPrice) {
		bars, err := r.ds.DailyLatest(context.Background(), codes, 2, nil)
		if err == nil {
			return bars, nil
		}
		if !r.dsFallback(datasource.DsDailyPrice, err) {
			return nil, err
		}
	}
	var bars []model.DailyPrice
	err := r.db.Raw(`
		SELECT t.code, t.trade_date, t.open, t.high, t.low, t.close, t.volume, t.amount, t.adj_factor
		FROM (
			SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
			FROM daily_price
			WHERE code IN ?
		) t
		WHERE t.rn <= 2
		ORDER BY t.code ASC, t.trade_date ASC`, codes).Scan(&bars).Error
	return bars, err
}

// adjOrOne 复权因子缺省：NULL（wire→0）→ 1（对齐 SQL COALESCE(adj_factor, 1)）。
// adj_factor 真实数据恒 > 0，故「==0」安全充当「空」判定。
func adjOrOne(f float64) float64 {
	if f == 0 {
		return 1
	}
	return f
}
