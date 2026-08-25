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

// GetFactor 因子定义（含 version/status/params）；不存在返回 gorm.ErrRecordNotFound
func (r *FactorRepository) GetFactor(name string) (*model.FactorDefinition, error) {
	var f model.FactorDefinition
	err := r.db.Where("name = ?", name).First(&f).Error
	if err != nil {
		return nil, err
	}
	return &f, nil
}

// ---- G10 FactorFactory 生命周期 CRUD（2.3b） ----

// ListFactors 因子定义全量（构建器因子池 + FactorFactory 管理列表，按权重倒序）
func (r *FactorRepository) ListFactors() ([]model.FactorDefinition, error) {
	var items []model.FactorDefinition
	err := r.db.Order("weight DESC, name").Find(&items).Error
	return items, err
}

// CreateFactor 新建因子；name 冲突返回唯一键错误
func (r *FactorRepository) CreateFactor(f *model.FactorDefinition) error {
	return r.db.Create(f).Error
}

// UpdateFactor 更新可编辑字段（category/description/formula/weight/params）
func (r *FactorRepository) UpdateFactor(f *model.FactorDefinition) error {
	return r.db.Model(f).Select("category", "description", "formula",
		"weight", "params").Updates(f).Error
}

// SetFactorStatus 更新状态（状态机约束在 service 层）
func (r *FactorRepository) SetFactorStatus(name, status string) error {
	return r.db.Model(&model.FactorDefinition{}).
		Where("name = ?", name).
		Update("status", status).Error
}

// CountFactorReferences 因子被引用数（factor_value 评分 + factor_stat 检验 + factor_trial 试算），
// 删除前置检查：任一非 0 即有历史留痕，不可删。
func (r *FactorRepository) CountFactorReferences(name string) (int64, error) {
	var n int64
	err := r.db.Table("factor_value").Where("factor_name = ?", name).Count(&n).Error
	if err != nil {
		return 0, err
	}
	if n > 0 {
		return n, nil
	}
	err = r.db.Table("factor_stat").Where("factor_name = ?", name).Count(&n).Error
	if err != nil {
		return 0, err
	}
	if n > 0 {
		return n, nil
	}
	err = r.db.Table("factor_trial").Where("factor_name = ?", name).Count(&n).Error
	return n, err
}

// DeleteFactor 删除因子（service 层已做状态与引用检查）
func (r *FactorRepository) DeleteFactor(name string) error {
	return r.db.Where("name = ?", name).Delete(&model.FactorDefinition{}).Error
}

// ---- G10 factor_trial 试算任务（2.3b，DB 队列：Go 提交 pending → 引擎消费） ----

// CreateTrial 创建试算任务（status 缺省 pending）
func (r *FactorRepository) CreateTrial(t *model.FactorTrial) (*model.FactorTrial, error) {
	if t.Status == "" {
		t.Status = "pending"
	}
	if err := r.db.Create(t).Error; err != nil {
		return nil, err
	}
	return t, nil
}

// GetTrial 试算任务详情；不存在返回 gorm.ErrRecordNotFound
func (r *FactorRepository) GetTrial(id uint64) (*model.FactorTrial, error) {
	var t model.FactorTrial
	err := r.db.First(&t, id).Error
	if err != nil {
		return nil, err
	}
	return &t, nil
}

// ListTrials 试算任务列表（可按因子过滤，按 id 倒序）
func (r *FactorRepository) ListTrials(factorName string, limit int) ([]model.FactorTrial, error) {
	var rows []model.FactorTrial
	q := r.db
	if factorName != "" {
		q = q.Where("factor_name = ?", factorName)
	}
	err := q.Order("id DESC").Limit(limit).Find(&rows).Error
	return rows, err
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
