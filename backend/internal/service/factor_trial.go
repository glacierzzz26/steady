package service

import (
	"encoding/json"
	"errors"
	"time"

	"gorm.io/gorm"

	"quant-system/backend/internal/model"
	"quant-system/backend/internal/repository"
)

// G10 factor_trial 试算/寻优任务服务（2.3b）：提交 → pending 入队，quant-engine 消费。
// 对齐 backtest_job 模式（设计定稿 §4.2/§6.2）：参数校验 + 幂等创建 + 状态查询。
//
// factor_trial.params JSONB 存储规约（引擎侧消费约定）：
//   - 单组试算：{"start","end","params":{...}}（params 缺省取因子定义 params 快照）
//   - 参数寻优：{"start","end","param_grid":{...}}
// kind 由 params 是否含 param_grid 区分（§5 不加列）。

var (
	ErrFactorTrialNotFound = errors.New("试算任务不存在")
	ErrFactorTrialRange    = errors.New("试算起始日不能晚于结束日")
	ErrFactorTrialSpan     = errors.New("试算区间不能超过5年")
	ErrFactorTrialGrid     = errors.New("param_grid 必须为非空 JSON 对象")
	ErrFactorTrialParams   = errors.New("params 必须为 JSON 对象")
)

// FactorTrialService 试算任务服务
type FactorTrialService struct {
	factorSvc *FactorService
	repo      *repository.FactorRepository
}

func NewFactorTrialService(factorSvc *FactorService, repo *repository.FactorRepository) *FactorTrialService {
	return &FactorTrialService{factorSvc: factorSvc, repo: repo}
}

// FactorTrialInput 单组试算请求（body 契约 §6.2：{params, start, end}）
type FactorTrialInput struct {
	Params json.RawMessage `json:"params"`
	Start  string          `json:"start"`
	End    string          `json:"end"`
}

// FactorOptimizeInput 参数寻优请求（body 契约 §6.2：{param_grid, start, end}）
type FactorOptimizeInput struct {
	ParamGrid json.RawMessage `json:"param_grid"`
	Start     string          `json:"start"`
	End       string          `json:"end"`
}

// CreateTrial 单组参数试算：创建 pending 任务（params 缺省取因子定义参数快照）
func (s *FactorTrialService) CreateTrial(name string, req FactorTrialInput) (*model.FactorTrial, error) {
	f, err := s.factorSvc.Get(name)
	if err != nil {
		return nil, err
	}
	start, end, err := parseTrialRange(req.Start, req.End)
	if err != nil {
		return nil, err
	}
	p := req.Params
	provided := len(p) > 0
	if !provided {
		// 缺省用因子定义 params 快照；仍为空（value/quality/risk 无计算参数）
		// → 空对象，engine 按「无计算参数」重算，不算非法输入
		p = json.RawMessage(f.Params)
		if len(p) == 0 {
			p = json.RawMessage("{}")
		}
	}
	if provided {
		// 用户显式提交的 params 必须是非空 JSON 对象
		if err := validateJSONObject(p, ErrFactorTrialParams); err != nil {
			return nil, err
		}
	}
	stored, err := json.Marshal(map[string]any{
		"start":  start.Format("2006-01-02"),
		"end":    end.Format("2006-01-02"),
		"params": json.RawMessage(p),
	})
	if err != nil {
		return nil, err
	}
	t := &model.FactorTrial{FactorName: name, Params: stored}
	return s.repo.CreateTrial(t)
}

// CreateOptimize 参数寻优：创建 pending 任务（params 存 param_grid 网格）
func (s *FactorTrialService) CreateOptimize(name string, req FactorOptimizeInput) (*model.FactorTrial, error) {
	if _, err := s.factorSvc.Get(name); err != nil {
		return nil, err
	}
	start, end, err := parseTrialRange(req.Start, req.End)
	if err != nil {
		return nil, err
	}
	if err := validateJSONObject(req.ParamGrid, ErrFactorTrialGrid); err != nil {
		return nil, err
	}
	stored, err := json.Marshal(map[string]any{
		"start":     start.Format("2006-01-02"),
		"end":       end.Format("2006-01-02"),
		"param_grid": json.RawMessage(req.ParamGrid),
	})
	if err != nil {
		return nil, err
	}
	t := &model.FactorTrial{FactorName: name, Params: stored}
	return s.repo.CreateTrial(t)
}

// Get 试算任务详情；不存在返回 ErrFactorTrialNotFound
func (s *FactorTrialService) Get(id uint64) (*model.FactorTrial, error) {
	t, err := s.repo.GetTrial(id)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, ErrFactorTrialNotFound
	}
	if err != nil {
		return nil, err
	}
	return t, nil
}

// List 试算任务列表（可按因子过滤）
func (s *FactorTrialService) List(factorName string, limit int) ([]model.FactorTrial, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	return s.repo.ListTrials(factorName, limit)
}

// ---- 内部 ----

// parseTrialRange 解析并校验区间（与 backtest 同纪律：start<end、≤5 年）
func parseTrialRange(startStr, endStr string) (start, end time.Time, err error) {
	if startStr == "" || endStr == "" {
		return time.Time{}, time.Time{}, errors.New("start/end 必填（YYYY-MM-DD）")
	}
	start, err = time.Parse("2006-01-02", startStr)
	if err != nil {
		return time.Time{}, time.Time{}, errors.New("start 格式应为 YYYY-MM-DD")
	}
	end, err = time.Parse("2006-01-02", endStr)
	if err != nil {
		return time.Time{}, time.Time{}, errors.New("end 格式应为 YYYY-MM-DD")
	}
	if !start.Before(end) {
		return time.Time{}, time.Time{}, ErrFactorTrialRange
	}
	if end.Sub(start) > maxBacktestSpanYears*365*24*time.Hour {
		return time.Time{}, time.Time{}, ErrFactorTrialSpan
	}
	return start, end, nil
}

// validateJSONObject raw 必须为非空 JSON 对象（trial params / optimize param_grid）
func validateJSONObject(raw json.RawMessage, errMsg error) error {
	if len(raw) == 0 {
		return errMsg
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil || len(m) == 0 {
		return errMsg
	}
	return nil
}
