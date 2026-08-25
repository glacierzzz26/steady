package service

import (
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"gorm.io/datatypes"
	"gorm.io/gorm"

	"quant-system/backend/internal/model"
	"quant-system/backend/internal/repository"
)

// G10 FactorFactory 因子生命周期服务（2.3b）：CRUD + 版本 fork + 状态流转。
// 复用 Iteration 4 策略状态机模式（strategy.go）；差异：
//   - 因子无「单 active」不变量（6 个已上线因子可同时 active，均进评分池）
//   - 编辑放宽到「草稿/停用」（契约 §6.2），停用因子可回炉重调
//   - 删除仅草稿，且因子已有 试算/评分/检验 记录则拒（历史留痕）
// 状态机：draft → trial → verified → active → disabled（disabled 可回 draft 或重新 active）。
// 约束：仅 active 因子进评分池——由 quant-engine factor_service 侧保证（引擎只算内置 6 因子，
// 变体因子为研究专用，见设计定稿 §4.3）。

var (
	ErrFactorExists       = errors.New("因子名已存在")
	ErrFactorNotEditable  = errors.New("仅草稿或停用状态可编辑")
	ErrFactorNotDeletable = errors.New("仅草稿状态可删除")
	ErrFactorHasRefs      = errors.New("因子已有试算/评分/检验记录，不可删除")
	ErrFactorTransition   = errors.New("非法的状态流转")
)

// factorTransitions 因子状态机允许的流转
// draft → trial（提交试算）/ disabled（放弃）；trial → verified（检验通过）/ draft（回炉）/ disabled；
// verified → active（上线）/ draft（回炉）/ disabled；active → disabled；disabled → draft / active。
var factorTransitions = map[string]map[string]bool{
	model.FactorStatusDraft: {
		model.FactorStatusTrial:    true,
		model.FactorStatusDisabled: true,
	},
	model.FactorStatusTrial: {
		model.FactorStatusVerified: true,
		model.FactorStatusDraft:    true,
		model.FactorStatusDisabled: true,
	},
	model.FactorStatusVerified: {
		model.FactorStatusActive:   true,
		model.FactorStatusDraft:    true,
		model.FactorStatusDisabled: true,
	},
	model.FactorStatusActive: {
		model.FactorStatusDisabled: true,
	},
	model.FactorStatusDisabled: {
		model.FactorStatusDraft:  true,
		model.FactorStatusActive: true,
	},
}

// FactorService 因子生命周期服务
type FactorService struct {
	repo *repository.FactorRepository
}

func NewFactorService(repo *repository.FactorRepository) *FactorService {
	return &FactorService{repo: repo}
}

// FactorInput 创建/更新因子请求体（name 仅创建时用；weight 指针区分「未提供」与 0）
type FactorInput struct {
	Name        string          `json:"name"`
	Category    string          `json:"category"`
	Description string          `json:"description"`
	Formula     string          `json:"formula"`
	Weight      *float64        `json:"weight"`
	Params      json.RawMessage `json:"params"`
}

// List 因子定义全量（构建器因子池 + FactorFactory 管理列表）
func (s *FactorService) List() ([]model.FactorDefinition, error) {
	return s.repo.ListFactors()
}

// Get 单个因子；不存在返回 ErrFactorNotFound
func (s *FactorService) Get(name string) (*model.FactorDefinition, error) {
	f, err := s.repo.GetFactor(name)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, ErrFactorNotFound
	}
	if err != nil {
		return nil, err
	}
	return f, nil
}

// Create 新建草稿因子（version v1.0）；name 冲突 / params 非 JSON 对象则拒
func (s *FactorService) Create(req FactorInput) (*model.FactorDefinition, error) {
	if _, err := s.repo.GetFactor(req.Name); err == nil {
		return nil, ErrFactorExists
	} else if !errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, err
	}
	if err := validateFactorParams(req.Params); err != nil {
		return nil, err
	}
	w := 0.0
	if req.Weight != nil {
		w = *req.Weight
	}
	f := &model.FactorDefinition{
		Name:        req.Name,
		Category:    req.Category,
		Description: req.Description,
		Formula:     req.Formula,
		Weight:      w,
		Version:     "v1.0",
		Status:      model.FactorStatusDraft,
		Params:      datatypes.JSON(req.Params),
		CreatedAt:   time.Now(),
	}
	if err := s.repo.CreateFactor(f); err != nil {
		// 唯一键兜底（并发下 Get 检查失效）
		return nil, ErrFactorExists
	}
	return f, nil
}

// Update 更新因子（仅 draft/disabled 可编辑）；weight/params 提供则覆盖
func (s *FactorService) Update(name string, req FactorInput) (*model.FactorDefinition, error) {
	f, err := s.Get(name)
	if err != nil {
		return nil, err
	}
	if f.Status != model.FactorStatusDraft && f.Status != model.FactorStatusDisabled {
		return nil, ErrFactorNotEditable
	}
	if err := validateFactorParams(req.Params); err != nil {
		return nil, err
	}
	if req.Category != "" {
		f.Category = req.Category
	}
	if req.Description != "" {
		f.Description = req.Description
	}
	if req.Formula != "" {
		f.Formula = req.Formula
	}
	if req.Weight != nil {
		f.Weight = *req.Weight
	}
	if req.Params != nil {
		f.Params = datatypes.JSON(req.Params)
	}
	if err := s.repo.UpdateFactor(f); err != nil {
		return nil, err
	}
	return s.Get(name)
}

// Fork 复制因子为新草稿版本（自动新 name：ma_trend → ma_trend_v2 → _v3…；
// version 提升 v1.0 → v2.0，params 参数快照随定义复制，契约 §6.2）
func (s *FactorService) Fork(name string) (*model.FactorDefinition, error) {
	f, err := s.Get(name)
	if err != nil {
		return nil, err
	}
	newName, err := s.nextForkName(f.Name)
	if err != nil {
		return nil, err
	}
	fork := &model.FactorDefinition{
		Name:        newName,
		Category:    f.Category,
		Description: fmt.Sprintf("由 %s 复制（fork）", f.Name),
		Formula:     f.Formula,
		Weight:      f.Weight,
		Version:     bumpVersion(f.Version),
		Status:      model.FactorStatusDraft,
		Params:      f.Params, // params 快照
		CreatedAt:   time.Now(),
	}
	if err := s.repo.CreateFactor(fork); err != nil {
		return nil, ErrFactorExists
	}
	return fork, nil
}

// Switch 状态流转（状态机约束见 factorTransitions）
func (s *FactorService) Switch(name, to string) (*model.FactorDefinition, error) {
	f, err := s.Get(name)
	if err != nil {
		return nil, err
	}
	if !validFactorStatus(to) || !factorTransitions[f.Status][to] {
		return nil, ErrFactorTransition
	}
	if err := s.repo.SetFactorStatus(name, to); err != nil {
		return nil, err
	}
	return s.Get(name)
}

// Delete 删除因子（仅草稿；已有试算/评分/检验记录则拒）
func (s *FactorService) Delete(name string) error {
	f, err := s.Get(name)
	if err != nil {
		return err
	}
	if f.Status != model.FactorStatusDraft {
		return ErrFactorNotDeletable
	}
	n, err := s.repo.CountFactorReferences(name)
	if err != nil {
		return err
	}
	if n > 0 {
		return ErrFactorHasRefs
	}
	return s.repo.DeleteFactor(name)
}

// ---- 内部 ----

// nextForkName 生成不冲突的 fork 名：ma_trend_v2, _v3, …
func (s *FactorService) nextForkName(base string) (string, error) {
	for n := 2; ; n++ {
		cand := fmt.Sprintf("%s_v%d", base, n)
		if _, err := s.repo.GetFactor(cand); errors.Is(err, gorm.ErrRecordNotFound) {
			return cand, nil
		} else if err != nil {
			return "", err
		}
	}
}

// validFactorStatus 校验合法状态值
func validFactorStatus(st string) bool {
	switch st {
	case model.FactorStatusDraft, model.FactorStatusTrial, model.FactorStatusVerified,
		model.FactorStatusActive, model.FactorStatusDisabled:
		return true
	}
	return false
}

// validateFactorParams params 若提供必须为 JSON 对象（变体因子参数，如 {"window":10}）
func validateFactorParams(raw json.RawMessage) error {
	if len(raw) == 0 {
		return nil
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return errors.New("params 必须为 JSON 对象")
	}
	return nil
}
