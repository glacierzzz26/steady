package service

import (
	"errors"
	"time"

	"gorm.io/gorm"

	"quant-system/backend/internal/model"
	"quant-system/backend/internal/repository"
)

// 回测任务校验错误
var (
	ErrBacktestRange     = errors.New("回测起始日不能晚于结束日")
	ErrBacktestSpan      = errors.New("回测区间不能超过5年")
	ErrBacktestTopN      = errors.New("目标持仓数应在1-50之间")
	ErrBacktestFillMode  = errors.New("fill_mode 仅支持 t_close / t1_open")
	ErrBacktestNotFound  = errors.New("回测任务不存在")
	ErrBacktestNoActive  = errors.New("无运行中策略，请先在策略管理激活一个策略")
)

// maxBacktestSpanYears 区间上限（内存重放预热 120 天，超长区间耗时线性增长）
const maxBacktestSpanYears = 5

// BacktestService 回测任务服务：参数校验 + 幂等创建 + 查询
// Iteration 4：支持按策略名回测（默认 active 策略，校验存在且非 archived）。
type BacktestService struct {
	repo        *repository.BacktestRepository
	strategyRepo *repository.StrategyRepository
}

func NewBacktestService(repo *repository.BacktestRepository, strategyRepo *repository.StrategyRepository) *BacktestService {
	return &BacktestService{repo: repo, strategyRepo: strategyRepo}
}

// CreateJob 校验并创建任务（幂等：同参数同假设返回已有任务）。
// strategyName 可选：为空解析 active 策略；提供则校验存在且非 archived（§3.3/§4.3）。
func (s *BacktestService) CreateJob(start, end time.Time, topN int, fillMode, strategyName string) (*model.BacktestJob, error) {
	if !start.Before(end) {
		return nil, ErrBacktestRange
	}
	if end.Sub(start) > maxBacktestSpanYears*365*24*time.Hour {
		return nil, ErrBacktestSpan
	}
	if topN < 1 || topN > 50 {
		return nil, ErrBacktestTopN
	}
	if fillMode == "" {
		fillMode = "t_close"
	}
	if fillMode != "t_close" && fillMode != "t1_open" {
		return nil, ErrBacktestFillMode
	}
	name, err := s.resolveStrategy(strategyName)
	if err != nil {
		return nil, err
	}
	j := &model.BacktestJob{
		StrategyName: name,
		StartDate:    start,
		EndDate:      end,
		TopN:         topN,
		FillMode:     fillMode,
	}
	return s.repo.CreateJob(j)
}

// resolveStrategy 解析回测策略名：空 → active 策略；提供 → 校验存在且非 archived
func (s *BacktestService) resolveStrategy(name string) (string, error) {
	if name == "" {
		active, err := s.strategyRepo.GetActive()
		if err != nil {
			return "", err
		}
		if active == nil {
			return "", ErrBacktestNoActive
		}
		return active.Name, nil
	}
	st, err := s.strategyRepo.Get(name)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return "", ErrStrategyNotFound
	}
	if err != nil {
		return "", err
	}
	if st.Status == model.StrategyArchived {
		return "", ErrStrategyArchived
	}
	return st.Name, nil
}

// List 最近任务列表
func (s *BacktestService) List(limit int) ([]model.BacktestJob, error) {
	return s.repo.ListJobs(limit)
}

// Get 任务详情；不存在返回 ErrBacktestNotFound
func (s *BacktestService) Get(id uint64) (*model.BacktestJob, error) {
	j, err := s.repo.GetJobDetail(id)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, ErrBacktestNotFound
	}
	if err != nil {
		return nil, err
	}
	return j, nil
}
