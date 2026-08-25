package repository

import (
	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"quant-system/backend/internal/model"
)

// StrategyRepository 策略数据访问层（Iteration 4：多策略生命周期）
// 命名冲突规避：model.Strategy 与 repository.StrategyRepository 不重名，可直接用。
type StrategyRepository struct {
	db *gorm.DB
}

func NewStrategyRepository(db *gorm.DB) *StrategyRepository {
	return &StrategyRepository{db: db}
}

// ListAll 全部策略（状态机全量，按创建时间倒序）
func (r *StrategyRepository) ListAll() ([]model.Strategy, error) {
	var items []model.Strategy
	err := r.db.Order("created_at DESC, id DESC").Find(&items).Error
	return items, err
}

// Get 按名称查询；不存在返回 gorm.ErrRecordNotFound
func (r *StrategyRepository) Get(name string) (*model.Strategy, error) {
	var s model.Strategy
	err := r.db.Where("name = ?", name).First(&s).Error
	if err != nil {
		return nil, err
	}
	return &s, nil
}

// GetActive 当前运行中策略；无 active 返回 (nil, nil)
func (r *StrategyRepository) GetActive() (*model.Strategy, error) {
	var s model.Strategy
	err := r.db.Where("status = ?", model.StrategyActive).First(&s).Error
	if err == gorm.ErrRecordNotFound {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &s, nil
}

// Create 新建策略；name 冲突返回唯一键错误
func (r *StrategyRepository) Create(s *model.Strategy) error {
	return r.db.Create(s).Error
}

// Update 更新草稿字段（factor_weights/params/description/zh_name），并刷新 updated_at
func (r *StrategyRepository) Update(s *model.Strategy) error {
	return r.db.Model(s).Updates(map[string]interface{}{
		"zh_name":        s.ZhName,
		"description":    s.Description,
		"factor_weights": s.FactorWeights,
		"params":         s.Params,
		"updated_at":     clause.Expr{SQL: "NOW()"},
	}).Error
}

// SetStatus 更新状态（状态机约束在 service 层）
func (r *StrategyRepository) SetStatus(name, status string) error {
	return r.db.Model(&model.Strategy{}).
		Where("name = ?", name).
		Update("status", status).Error
}

// SwitchTx 状态流转（事务）：切换为 active 时先把当前 active 降级为 paused，
// 保证「同一时间仅一个运行中」不变量原子生效。
func (r *StrategyRepository) SwitchTx(name, to string) error {
	return r.db.Transaction(func(tx *gorm.DB) error {
		if to == model.StrategyActive {
			if err := tx.Model(&model.Strategy{}).
				Where("status = ? AND name <> ?", model.StrategyActive, name).
				Update("status", model.StrategyPaused).Error; err != nil {
				return err
			}
		}
		return tx.Model(&model.Strategy{}).
			Where("name = ?", name).
			Update("status", to).Error
	})
}

// CountStrategySignals 该策略已有信号记录数（删除前置检查：strategy_signal 有 FK 引用 strategy.name）
func (r *StrategyRepository) CountStrategySignals(name string) (int64, error) {
	var n int64
	err := r.db.Table("strategy_signal").Where("strategy_name = ?", name).Count(&n).Error
	return n, err
}

// Delete 删除策略（service 层已做状态与信号引用检查）
func (r *StrategyRepository) Delete(name string) error {
	return r.db.Where("name = ?", name).Delete(&model.Strategy{}).Error
}

// GetLatestBacktestID 策略最近一次回测任务 id（策略卡「回测依据」展示用）；无返回 0
func (r *StrategyRepository) GetLatestBacktestID(strategy string) (uint64, error) {
	var id uint64
	err := r.db.Table("backtest_job").
		Where("strategy_name = ?", strategy).
		Order("id DESC").Limit(1).
		Pluck("id", &id).Error
	if err != nil {
		return 0, err
	}
	return id, nil
}
