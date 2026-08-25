package repository

import (
	"time"

	"gorm.io/gorm"

	"quant-system/backend/internal/model"
)

// FactorRepository 因子检验统计数据访问层（2.3 G9：读 factor_stat/factor_corr 表）
// 只读预计算结果，不做 IC 数学（单实现在 quant-engine，见 docs 设计定稿 §4.2）。
type FactorRepository struct {
	db *gorm.DB
}

func NewFactorRepository(db *gorm.DB) *FactorRepository {
	return &FactorRepository{db: db}
}

// GetFactor 因子定义（含 version/status）；不存在返回 gorm.ErrRecordNotFound
func (r *FactorRepository) GetFactor(name string) (*model.FactorDefinition, error) {
	var f model.FactorDefinition
	err := r.db.Where("name = ?", name).First(&f).Error
	if err != nil {
		return nil, err
	}
	return &f, nil
}

// GetStat 因子检验统计（per-date 追加行，升序）
func (r *FactorRepository) GetStat(name string, start, end time.Time) ([]model.FactorStat, error) {
	var rows []model.FactorStat
	err := r.db.Where("factor_name = ? AND trade_date BETWEEN ? AND ?",
		name, start.Format("2006-01-02"), end.Format("2006-01-02")).
		Order("trade_date").Find(&rows).Error
	return rows, err
}

// GetCorr 相关性矩阵（per-date 6×6 JSONB，升序）
func (r *FactorRepository) GetCorr(start, end time.Time) ([]model.FactorCorr, error) {
	var rows []model.FactorCorr
	err := r.db.Where("trade_date BETWEEN ? AND ?",
		start.Format("2006-01-02"), end.Format("2006-01-02")).
		Order("trade_date").Find(&rows).Error
	return rows, err
}
