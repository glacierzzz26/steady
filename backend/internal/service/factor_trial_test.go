package service

import (
	"encoding/json"
	"testing"

	"quant-system/backend/internal/repository"
)

// newFactorTrialSvc 复用 factorManageDB（factor_definition + factor_trial 均已建）
func newFactorTrialSvc(t *testing.T) (*FactorTrialService, *FactorService) {
	t.Helper()
	db := factorManageDB(t)
	factorSvc := NewFactorService(repository.NewFactorRepository(db))
	trialSvc := NewFactorTrialService(factorSvc, repository.NewFactorRepository(db))
	return trialSvc, factorSvc
}

func TestFactorTrialLifecycle(t *testing.T) {
	trialSvc, factorSvc := newFactorTrialSvc(t)

	// 因子带 params 快照（变体：ma_trend 窗口 10）
	if _, err := factorSvc.Create(FactorInput{
		Name: "ma_trend_ma10", Category: "trend", Formula: "MA10>MA20",
		Params: json.RawMessage(`{"window":10}`),
	}); err != nil {
		t.Fatalf("seed factor: %v", err)
	}

	// 1. 单组试算：pending + params 合并 start/end
	tr, err := trialSvc.CreateTrial("ma_trend_ma10", FactorTrialInput{
		Params: json.RawMessage(`{"window":20}`),
		Start:  "2026-01-01", End: "2026-06-30",
	})
	if err != nil {
		t.Fatalf("CreateTrial: %v", err)
	}
	if tr.Status != "pending" || tr.ID == 0 {
		t.Fatalf("trial 应 pending 且返回 id: %+v", tr)
	}
	var stored map[string]any
	if err := json.Unmarshal(tr.Params, &stored); err != nil {
		t.Fatalf("params JSON 解析: %v", err)
	}
	if stored["start"] != "2026-01-01" || stored["end"] != "2026-06-30" {
		t.Fatalf("params 应含 start/end: %s", tr.Params)
	}

	// 2. params 缺省 → 取因子定义 params 快照（window:10）
	tr2, err := trialSvc.CreateTrial("ma_trend_ma10", FactorTrialInput{
		Start: "2026-01-01", End: "2026-06-30",
	})
	if err != nil {
		t.Fatalf("CreateTrial no params: %v", err)
	}
	var p2 map[string]any
	_ = json.Unmarshal(tr2.Params, &p2)
	inner, _ := p2["params"].(map[string]any)
	if inner["window"] != float64(10) {
		t.Fatalf("缺省 params 应取因子快照 window=10, got %s", tr2.Params)
	}

	// 3. 校验错误
	if _, err := trialSvc.CreateTrial("ma_trend_ma10", FactorTrialInput{
		Start: "2026-06-30", End: "2026-01-01",
	}); err != ErrFactorTrialRange {
		t.Fatalf("start>end 应拒绝，got %v", err)
	}
	if _, err := trialSvc.CreateTrial("ma_trend_ma10", FactorTrialInput{
		Start: "2020-01-01", End: "2026-06-30",
	}); err != ErrFactorTrialSpan {
		t.Fatalf(">5 年应拒绝，got %v", err)
	}
	if _, err := trialSvc.CreateTrial("ma_trend_ma10", FactorTrialInput{
		Params: json.RawMessage(`[1,2]`), Start: "2026-01-01", End: "2026-06-30",
	}); err != ErrFactorTrialParams {
		t.Fatalf("params 非对象应拒绝，got %v", err)
	}
	if _, err := trialSvc.CreateTrial("no_such_factor", FactorTrialInput{
		Start: "2026-01-01", End: "2026-06-30",
	}); err != ErrFactorNotFound {
		t.Fatalf("未知因子应 ErrFactorNotFound，got %v", err)
	}

	// 4. 寻优：param_grid 存网格
	op, err := trialSvc.CreateOptimize("ma_trend_ma10", FactorOptimizeInput{
		ParamGrid: json.RawMessage(`{"window":[5,10,20],"horizon":[5,10,20]}`),
		Start:     "2026-01-01", End: "2026-06-30",
	})
	if err != nil {
		t.Fatalf("CreateOptimize: %v", err)
	}
	var o map[string]any
	if err := json.Unmarshal(op.Params, &o); err != nil {
		t.Fatalf("optimize params 解析: %v", err)
	}
	if _, ok := o["param_grid"]; !ok {
		t.Fatalf("optimize params 应含 param_grid: %s", op.Params)
	}
	if _, err := trialSvc.CreateOptimize("ma_trend_ma10", FactorOptimizeInput{
		Start: "2026-01-01", End: "2026-06-30",
	}); err != ErrFactorTrialGrid {
		t.Fatalf("空 param_grid 应拒绝，got %v", err)
	}

	// 5. Get
	got, err := trialSvc.Get(tr.ID)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.ID != tr.ID {
		t.Fatalf("Get 返回错误任务: %d != %d", got.ID, tr.ID)
	}
	if _, err := trialSvc.Get(999999); err != ErrFactorTrialNotFound {
		t.Fatalf("未知 id 应 ErrFactorTrialNotFound，got %v", err)
	}

	// 6. List 按因子过滤
	items, err := trialSvc.List("ma_trend_ma10", 10)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(items) < 2 {
		t.Fatalf("List 应含多个 trial，got %d", len(items))
	}
	// 倒序：最新在前
	if items[0].ID < items[len(items)-1].ID {
		t.Fatal("List 应按 id 倒序")
	}
}

// TestTrialValueFactorEmptyParams：value/quality/risk 因子无计算参数快照（null），
// 试算缺省 params → 服务默认路径产出空对象 {}，必须放行（e2e 抓到的回归：
// validateJSONObject 拒空对象，把服务自身默认路径也一起拒了，value 因子无法试算）。
func TestTrialValueFactorEmptyParams(t *testing.T) {
	trialSvc, factorSvc := newFactorTrialSvc(t)

	// 无 params 快照的 value 因子（pe_ratio 类）
	if _, err := factorSvc.Create(FactorInput{
		Name: "pe_ratio", Category: "value", Formula: "PE 倒数",
	}); err != nil {
		t.Fatalf("seed factor: %v", err)
	}

	// 1. 缺省 params → 空对象 {} 应被接受（value 因子无计算参数）
	tr, err := trialSvc.CreateTrial("pe_ratio", FactorTrialInput{
		Start: "2026-01-01", End: "2026-06-30",
	})
	if err != nil {
		t.Fatalf("value 因子缺省 params 试算应放行，got %v", err)
	}
	var stored map[string]any
	if err := json.Unmarshal(tr.Params, &stored); err != nil {
		t.Fatalf("params JSON 解析: %v", err)
	}
	inner, ok := stored["params"].(map[string]any)
	if !ok || len(inner) != 0 {
		t.Fatalf("缺省 params 应落空对象 {}，got %#v", stored["params"])
	}

	// 2. 用户显式提交空对象 {} 仍应拒绝（与非对象等同非法输入）
	if _, err := trialSvc.CreateTrial("pe_ratio", FactorTrialInput{
		Params: json.RawMessage(`{}`), Start: "2026-01-01", End: "2026-06-30",
	}); err != ErrFactorTrialParams {
		t.Fatalf("显式空 params 应拒绝，got %v", err)
	}
}
