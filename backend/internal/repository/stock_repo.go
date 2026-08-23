package repository

import (
	"gorm.io/gorm"

	"quant-system/backend/internal/model"
)

// StockRepository 股票数据访问层
type StockRepository struct {
	db *gorm.DB
}

func NewStockRepository(db *gorm.DB) *StockRepository {
	return &StockRepository{db: db}
}

// StockListQuery 股票列表查询条件
type StockListQuery struct {
	Page     int
	PageSize int
	Industry string // 行业精确匹配
	Keyword  string // 代码/名称模糊匹配（ILIKE）
	Market   string // SH / SZ / BJ
	Universe string // hs300 / zz500 / NULL
	Sort     string // 白名单：code / name / list_date / market / industry
	Order    string // asc / desc
}

// GetList 分页查询股票列表，支持行业/关键词/市场/股票池过滤与白名单排序
func (r *StockRepository) GetList(q StockListQuery) ([]model.StockBasic, int64, error) {
	var stocks []model.StockBasic
	var total int64

	query := r.db.Model(&model.StockBasic{})
	if q.Industry != "" {
		query = query.Where("industry = ?", q.Industry)
	}
	if q.Keyword != "" {
		kw := "%" + q.Keyword + "%"
		query = query.Where("name ILIKE ? OR code ILIKE ?", kw, kw)
	}
	if q.Market != "" {
		query = query.Where("market = ?", q.Market)
	}
	if q.Universe != "" {
		query = query.Where("universe = ?", q.Universe)
	}
	if err := query.Count(&total).Error; err != nil {
		return nil, 0, err
	}

	err := query.Order(stockSortClause(q.Sort, q.Order)).
		Offset((q.Page - 1) * q.PageSize).
		Limit(q.PageSize).
		Find(&stocks).Error
	return stocks, total, err
}

// GetByCode 按代码查询股票，未找到返回 (nil, nil)
func (r *StockRepository) GetByCode(code string) (*model.StockBasic, error) {
	var stock model.StockBasic
	err := r.db.Where("code = ?", code).First(&stock).Error
	if err == gorm.ErrRecordNotFound {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &stock, nil
}

// Exists 股票是否存在
func (r *StockRepository) Exists(code string) (bool, error) {
	var count int64
	if err := r.db.Model(&model.StockBasic{}).Where("code = ?", code).Count(&count).Error; err != nil {
		return false, err
	}
	return count > 0, nil
}

// 排序白名单：非法值回退 code ASC（值全部来自白名单，拼接 Order 无注入风险）
var stockSortColumns = map[string]string{
	"code":      "code",
	"name":      "name",
	"list_date": "list_date",
	"market":    "market",
	"industry":  "industry",
}

func stockSortClause(sort, order string) string {
	col, ok := stockSortColumns[sort]
	if !ok {
		col = "code"
	}
	// NULLS LAST：真实数据中 list_date 等字段可能为空，空值不应排在有效值前
	if order == "desc" {
		return col + " DESC NULLS LAST"
	}
	return col + " ASC NULLS LAST"
}

// ---- G2 列表扩展：行情 / 估值 / 财务（批量一次 JOIN，避免 N+1）----
// 缺失字段一律为 nil（前端空态「—」，不造假）

// PoolMarket 最新行情（G2）：price / chg(%) / amount(元)
type PoolMarket struct {
	Price  *float64
	Chg    *float64
	Amount *float64
}

// GetPoolMarket 批量最新行情 + 涨跌幅
// chg = 相对前一交易日收盘的百分比（daily_price 无涨跌列，需 LAG(close) 现算）
func (r *StockRepository) GetPoolMarket(codes []string) (map[string]*PoolMarket, error) {
	out := make(map[string]*PoolMarket, len(codes))
	if len(codes) == 0 {
		return out, nil
	}
	type row struct {
		Code   string
		Price  float64
		Chg    *float64
		Amount float64
	}
	var rows []row
	err := r.db.Raw(`
		WITH ranked AS (
			SELECT code, close, amount,
				LAG(close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_close,
				ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
			FROM daily_price
			WHERE code IN (?)
		)
		SELECT code, close AS price, amount,
			CASE WHEN prev_close IS NOT NULL AND prev_close > 0
				 THEN (close / prev_close - 1) * 100 END AS chg
		FROM ranked WHERE rn = 1`, codes).Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	for _, rr := range rows {
		out[rr.Code] = &PoolMarket{Price: &rr.Price, Chg: rr.Chg, Amount: &rr.Amount}
	}
	return out, nil
}

// PoolValuation 最新估值（G2）：pe_ttm / pb
type PoolValuation struct {
	Pe *float64
	Pb *float64
}

// GetPoolValuation 批量最新日度估值（trade_date 倒序取最近一条）
func (r *StockRepository) GetPoolValuation(codes []string) (map[string]*PoolValuation, error) {
	out := make(map[string]*PoolValuation, len(codes))
	if len(codes) == 0 {
		return out, nil
	}
	type row struct {
		Code string
		Pe   *float64
		Pb   *float64
	}
	var rows []row
	err := r.db.Raw(`
		WITH ranked AS (
			SELECT code, pe_ttm, pb,
				ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
			FROM daily_valuation
			WHERE code IN (?)
		)
		SELECT code, pe_ttm AS pe, pb FROM ranked
		WHERE rn = 1 AND (pe_ttm IS NOT NULL OR pb IS NOT NULL)`, codes).Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	for _, rr := range rows {
		out[rr.Code] = &PoolValuation{Pe: rr.Pe, Pb: rr.Pb}
	}
	return out, nil
}

// GetNames 批量查询股票名称（G4 委托/成交补名用），key: code；查不到的代码缺失
func (r *StockRepository) GetNames(codes []string) (map[string]string, error) {
	out := make(map[string]string, len(codes))
	if len(codes) == 0 {
		return out, nil
	}
	type row struct {
		Code string
		Name string
	}
	var rows []row
	err := r.db.Model(&model.StockBasic{}).
		Select("code, name").
		Where("code IN (?)", codes).
		Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	for _, rr := range rows {
		out[rr.Code] = rr.Name
	}
	return out, nil
}

// GetIndustries 批量查询股票行业（风控行业集中度用），key: code；查不到的代码缺失
func (r *StockRepository) GetIndustries(codes []string) (map[string]string, error) {
	out := make(map[string]string, len(codes))
	if len(codes) == 0 {
		return out, nil
	}
	type row struct {
		Code     string
		Industry string
	}
	var rows []row
	err := r.db.Model(&model.StockBasic{}).
		Select("code, industry").
		Where("code IN (?)", codes).
		Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	for _, rr := range rows {
		out[rr.Code] = rr.Industry
	}
	return out, nil
}

// PoolFinancial 财务摘要（G2）：roe（公告日最新，同详情口径）
type PoolFinancial struct {
	Roe *float64
}

// GetPoolFinancial 批量最新 ROE（announce_date DESC, report_date DESC 取最近一条）
func (r *StockRepository) GetPoolFinancial(codes []string) (map[string]*PoolFinancial, error) {
	out := make(map[string]*PoolFinancial, len(codes))
	if len(codes) == 0 {
		return out, nil
	}
	type row struct {
		Code string
		Roe  *float64
	}
	var rows []row
	err := r.db.Raw(`
		WITH ranked AS (
			SELECT code, roe,
				ROW_NUMBER() OVER (PARTITION BY code ORDER BY announce_date DESC, report_date DESC) AS rn
			FROM financial_indicator
			WHERE code IN (?) AND announce_date IS NOT NULL
		)
		SELECT code, roe FROM ranked WHERE rn = 1 AND roe IS NOT NULL`, codes).Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	for _, rr := range rows {
		out[rr.Code] = &PoolFinancial{Roe: rr.Roe}
	}
	return out, nil
}
