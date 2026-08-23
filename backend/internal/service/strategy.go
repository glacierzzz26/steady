package service

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"gorm.io/datatypes"
	"gorm.io/gorm"

	"quant-system/backend/internal/model"
	"quant-system/backend/internal/repository"
)

// 策略生命周期错误（handler 映射 400/404）
var (
	ErrStrategyNotFound    = errors.New("策略不存在")
	ErrStrategyExists      = errors.New("策略名已存在")
	ErrStrategyNotDraft    = errors.New("仅草稿状态可编辑")
	ErrStrategyTransition  = errors.New("非法的状态流转")
	ErrStrategyArchived    = errors.New("已归档策略不可编辑或激活")
	ErrStrategyInvalidData = errors.New("factor_weights 必须为非空 JSON 对象")
)

// strategyTransitions 状态机允许的流转（draft → backtest → sample → active → paused/archived）
// active → paused 才可 → archived；任何中间态可回 draft（回炉）。
var strategyTransitions = map[string]map[string]bool{
	model.StrategyDraft: {
		model.StrategyBacktest: true,
		model.StrategyArchived: true,
	},
	model.StrategyBacktest: {
		model.StrategySample: true,
		model.StrategyDraft:  true,
		model.StrategyArchived: true,
	},
	model.StrategySample: {
		model.StrategyActive: true,
		model.StrategyDraft:  true,
		model.StrategyArchived: true,
	},
	model.StrategyActive: {
		model.StrategyPaused: true,
	},
	model.StrategyPaused: {
		model.StrategyActive:   true,
		model.StrategyArchived: true,
	},
	model.StrategyArchived: {},
}

// StrategyService 策略生命周期：CRUD + 状态机 + fork 版本。
// 不变量：同一时间仅一个 status='active' 的策略（切换时事务内降级旧 active）。
type StrategyService struct {
	repo       *repository.StrategyRepository
	backtest   *BacktestService
	signalRepo *repository.SignalRepository
}

func NewStrategyService(repo *repository.StrategyRepository, backtest *BacktestService) *StrategyService {
	return &StrategyService{repo: repo, backtest: backtest}
}

// StrategyItem 策略 + 最近回测任务 id（策略卡「回测依据」）
type StrategyItem struct {
	model.Strategy
	LatestBacktestID uint64 `json:"latest_backtest_id"`
}

// List 全部策略（含元数据与最近回测 id）
func (s *StrategyService) List() ([]StrategyItem, error) {
	items, err := s.repo.ListAll()
	if err != nil {
		return nil, err
	}
	out := make([]StrategyItem, 0, len(items))
	for _, st := range items {
		id, err := s.repo.GetLatestBacktestID(st.Name)
		if err != nil {
			return nil, err
		}
		out = append(out, StrategyItem{Strategy: st, LatestBacktestID: id})
	}
	return out, nil
}

// Get 单个策略
func (s *StrategyService) Get(name string) (*model.Strategy, error) {
	st, err := s.repo.Get(name)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, ErrStrategyNotFound
	}
	if err != nil {
		return nil, err
	}
	return st, nil
}

// Create 新建草稿策略；factor_weights 非空对象（因子级权重映射）
func (s *StrategyService) Create(req StrategyInput) (*model.Strategy, error) {
	if _, err := s.repo.Get(req.Name); err == nil {
		return nil, ErrStrategyExists
	}
	if err := validateFactorWeights(req.FactorWeights); err != nil {
		return nil, err
	}
	now := time.Now()
	st := &model.Strategy{
		Name:          req.Name,
		ZhName:        req.ZhName,
		Description:   req.Description,
		Version:       "v1.0",
		FactorWeights: datatypes.JSON(req.FactorWeights),
		Params:        datatypes.JSON(req.Params),
		Status:        model.StrategyDraft,
		CreatedAt:     now,
		UpdatedAt:     now,
	}
	if err := s.repo.Create(st); err != nil {
		return nil, ErrStrategyExists
	}
	return st, nil
}

// Update 更新草稿（仅 draft 可编辑）；factor_weights 提供则覆盖（非空对象校验）
func (s *StrategyService) Update(name string, req StrategyInput) (*model.Strategy, error) {
	st, err := s.Get(name)
	if err != nil {
		return nil, err
	}
	if st.Status != model.StrategyDraft {
		return nil, ErrStrategyNotDraft
	}
	if req.ZhName != "" {
		st.ZhName = req.ZhName
	}
	if req.Description != "" {
		st.Description = req.Description
	}
	if req.FactorWeights != nil {
		if err := validateFactorWeights(req.FactorWeights); err != nil {
			return nil, err
		}
		st.FactorWeights = datatypes.JSON(req.FactorWeights)
	}
	if req.Params != nil {
		st.Params = datatypes.JSON(req.Params)
	}
	if err := s.repo.Update(st); err != nil {
		return nil, err
	}
	return s.Get(name)
}

// Fork 复制当前策略为新草稿版本（自动新 name：multi_factor → multi_factor_v2 → _v3…）
func (s *StrategyService) Fork(name string) (*model.Strategy, error) {
	st, err := s.Get(name)
	if err != nil {
		return nil, err
	}
	if st.Status == model.StrategyArchived {
		return nil, ErrStrategyArchived
	}
	newName, err := s.nextForkName(st.Name)
	if err != nil {
		return nil, err
	}
	now := time.Now()
	fork := &model.Strategy{
		Name:          newName,
		ZhName:        st.ZhName + " 候选",
		Description:   fmt.Sprintf("由 %s 复制（fork）", st.Name),
		Version:       bumpVersion(st.Version),
		FactorWeights: st.FactorWeights,
		Params:        st.Params,
		Status:        model.StrategyDraft,
		CreatedAt:     now,
		UpdatedAt:     now,
	}
	if err := s.repo.Create(fork); err != nil {
		return nil, err
	}
	return fork, nil
}

// Switch 状态流转（单 active 不变量）；切换到 active 时事务内降级旧 active 为 paused
func (s *StrategyService) Switch(name, to string) (*model.Strategy, error) {
	st, err := s.Get(name)
	if err != nil {
		return nil, err
	}
	if !validStrategyStatus(to) {
		return nil, ErrStrategyTransition
	}
	if !strategyTransitions[st.Status][to] {
		return nil, ErrStrategyTransition
	}
	err = s.repo.SwitchTx(name, to)
	if err != nil {
		return nil, err
	}
	return s.Get(name)
}

// StrategyInput 创建/更新请求体
type StrategyInput struct {
	Name          string          `json:"name"`
	ZhName        string          `json:"zh_name"`
	Description   string          `json:"description"`
	FactorWeights json.RawMessage `json:"factor_weights"`
	Params        json.RawMessage `json:"params"`
}

// CompareOutcome A/B 对比结果（§3.3）：status pending → 前端轮询；done → 两侧 job
// （含 Result，handler 组装 DTO）；failed → 有任务失败。
type CompareOutcome struct {
	Status string             // pending / done / failed
	Base   *model.BacktestJob `json:"-"`
	Cand   *model.BacktestJob `json:"-"`
}

// Compare A/B 对比：同区间同假设为两个策略各建回测 job（幂等复用已有结果），
// 校验两侧存在且非 archived；每个策略用自己的 params.top_n（缺省 20）。
func (s *StrategyService) Compare(base, cand string, start, end time.Time, fillMode string) (*CompareOutcome, error) {
	for _, name := range []string{base, cand} {
		st, err := s.Get(name)
		if err != nil {
			return nil, err
		}
		if st.Status == model.StrategyArchived {
			return nil, ErrStrategyArchived
		}
	}
	baseJob, err := s.backtest.CreateJob(start, end, strategyTopN(base, s), fillMode, base)
	if err != nil {
		return nil, err
	}
	candJob, err := s.backtest.CreateJob(start, end, strategyTopN(cand, s), fillMode, cand)
	if err != nil {
		return nil, err
	}
	bd, err := s.backtest.Get(baseJob.ID)
	if err != nil {
		return nil, err
	}
	cd, err := s.backtest.Get(candJob.ID)
	if err != nil {
		return nil, err
	}
	// 任一任务未完成 → 前端轮询；任一失败 → 标记 failed（handler 报错）
	status := "done"
	for _, j := range []*model.BacktestJob{bd, cd} {
		if j.Status == "pending" || j.Status == "running" {
			status = "pending"
		} else if j.Status == "failed" {
			status = "failed"
		}
	}
	return &CompareOutcome{Status: status, Base: bd, Cand: cd}, nil
}

// strategyTopN 从策略 params 解析 top_n（缺省 20；非法值回退缺省）
func strategyTopN(name string, s *StrategyService) int {
	st, err := s.Get(name)
	if err != nil {
		return 20
	}
	var p struct {
		TopN int `json:"top_n"`
	}
	if len(st.Params) == 0 || json.Unmarshal(st.Params, &p) != nil || p.TopN < 1 || p.TopN > 50 {
		return 20
	}
	return p.TopN
}

// ---- 内部 ----

// nextForkName 生成不冲突的 fork 名：multi_factor_v2, _v3, …
func (s *StrategyService) nextForkName(base string) (string, error) {
	for n := 2; ; n++ {
		cand := fmt.Sprintf("%s_v%d", base, n)
		if _, err := s.repo.Get(cand); errors.Is(err, gorm.ErrRecordNotFound) {
			return cand, nil
		} else if err != nil {
			return "", err
		}
	}
}

// validStrategyStatus 校验合法状态值
func validStrategyStatus(st string) bool {
	switch st {
	case model.StrategyDraft, model.StrategyBacktest, model.StrategySample,
		model.StrategyActive, model.StrategyPaused, model.StrategyArchived:
		return true
	}
	return false
}

// validateFactorWeights factor_weights 必须为非空 JSON 对象（数字权重）
func validateFactorWeights(raw json.RawMessage) error {
	if len(raw) == 0 {
		return nil // 允许省略（创建时引擎回退 factor_definition）
	}
	var m map[string]float64
	if err := json.Unmarshal(raw, &m); err != nil || len(m) == 0 {
		return ErrStrategyInvalidData
	}
	return nil
}

var versionRe = regexp.MustCompile(`^v(\d+)\.(\d+)$`)

// bumpVersion v1.0 → v2.0；无法解析时原样追加 .1
func bumpVersion(v string) string {
	if m := versionRe.FindStringSubmatch(v); m != nil {
		major, _ := strconv.Atoi(m[1])
		return fmt.Sprintf("v%d.0", major+1)
	}
	return strings.TrimSpace(v) + ".1"
}
