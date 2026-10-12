package repository

import (
	"context"
	"log"
	"sort"

	"gorm.io/gorm"

	"quant-system/backend/internal/datasource"
	"quant-system/backend/internal/model"
)

// FinancialRepository 财务指标数据访问层（只读）
//
// ds（datahub 读取源）为可选：nil ⇒ 恒走本地 gorm（含全部 tx-scoped 构造）。
// 非 nil 且 financial_indicator 闸门开 ⇒ 命中读点走 datahub HTTP，其余读点仍本地。
type FinancialRepository struct {
	db *gorm.DB
	ds *datasource.Source
}

// NewFinancialRepository 构造。ds 变参可选：现有 `NewFinancialRepository(db)` 调用点
// 零改动（不传 ⇒ nil ⇒ 纯本地）。
func NewFinancialRepository(db *gorm.DB, ds ...*datasource.Source) *FinancialRepository {
	r := &FinancialRepository{db: db}
	if len(ds) > 0 {
		r.ds = ds[0]
	}
	return r
}

// useDS financial_indicator 是否走 datahub（闸门判定）。
func (r *FinancialRepository) useDS() bool {
	return r.ds != nil && r.ds.Enabled(datasource.DsFinancialIndicator)
}

// dsFallback 读失败处置：开 fallback ⇒ 记 WARNING 并回退本地（返回 true）；
// 否则失败即抛（返回 false，不静默给陈旧值）。
func (r *FinancialRepository) dsFallback(err error) bool {
	if r.ds.FallbackLocal() {
		log.Printf("WARN datahub financial_indicator 读失败，回退本地: %v", err)
		return true
	}
	return false
}

// GetByCode 按报告期倒序取财务指标，最多 limit 条
func (r *FinancialRepository) GetByCode(code string, limit int) ([]model.FinancialIndicator, error) {
	if r.useDS() {
		rows, err := r.ds.FinancialByCodes(context.Background(), []string{code})
		if err == nil {
			// datahub 返回 (code, report_date ASC)；本读点语义为 report_date DESC。
			sort.Slice(rows, func(i, j int) bool {
				return rows[i].ReportDate.After(rows[j].ReportDate)
			})
			if limit > 0 && len(rows) > limit {
				rows = rows[:limit]
			}
			return rows, nil
		}
		if !r.dsFallback(err) {
			return nil, err
		}
	}
	var items []model.FinancialIndicator
	err := r.db.Where("code = ?", code).
		Order("report_date DESC").
		Limit(limit).
		Find(&items).Error
	return items, err
}

// GetLatestByAnnounce 按公告日倒序取最近一条（详情页财务摘要），无数据返回 (nil, nil)
// announce_date 相同时按 report_date 倒序兜底，保证结果稳定
func (r *FinancialRepository) GetLatestByAnnounce(code string) (*model.FinancialIndicator, error) {
	if r.useDS() {
		rows, err := r.ds.FinancialLast(context.Background(), []string{code}, nil, 1)
		if err == nil {
			if len(rows) == 0 {
				return nil, nil
			}
			item := rows[len(rows)-1] // last=1 → 每码恰一行
			return &item, nil
		}
		if !r.dsFallback(err) {
			return nil, err
		}
	}
	var item model.FinancialIndicator
	err := r.db.Where("code = ?", code).
		Order("announce_date DESC, report_date DESC").
		First(&item).Error
	if err == gorm.ErrRecordNotFound {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &item, nil
}
