package service

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"

	"quant-system/backend/internal/model"
	"quant-system/backend/internal/repository"
)

// strategyDB 策略生命周期集成测试库（TEST_DB_DSN 门控，模式对齐 freshDB）：
//   - 未设 TEST_DB_DSN 时跳过
//   - 拒绝连接生产库 quant_system，必须用独立测试库
//   - 每次先删后建 strategy/backtest_job，保证可重复执行
func strategyDB(t *testing.T) *gorm.DB {
	t.Helper()
	dsn := os.Getenv("TEST_DB_DSN")
	if dsn == "" {
		t.Skip("TEST_DB_DSN 未设置，跳过集成测试（本地/CI 设置后自动启用）")
	}
	if strings.Contains(dsn, "dbname=quant_system") && !strings.Contains(dsn, "dbname=quant_system_test") {
		t.Fatal("拒绝连接生产库 quant_system，请使用独立测试库 quant_system_test")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{})
	if err != nil {
		t.Fatalf("连接测试库失败: %v", err)
	}
	// backtest_result 外键引用 backtest_job（CASCADE），先删结果表
	if err := db.Migrator().DropTable(&model.BacktestResult{}); err != nil {
		t.Fatalf("清理 backtest_result 失败: %v", err)
	}
	if err := db.Migrator().DropTable(&model.Strategy{}, &model.BacktestJob{}, &model.StrategySignal{}); err != nil {
		t.Fatalf("清理测试表失败: %v", err)
	}
	if err := db.AutoMigrate(&model.Strategy{}, &model.BacktestJob{}, &model.BacktestResult{}, &model.StrategySignal{}); err != nil {
		t.Fatalf("AutoMigrate 失败: %v", err)
	}
	// backtest_job 幂等唯一键（生产在 init.sql，测试库 AutoMigrate 不建复合唯一索引）
	if err := db.Exec(`CREATE UNIQUE INDEX IF NOT EXISTS uq_backtest_job
		ON backtest_job (strategy_name, start_date, end_date, top_n, fill_mode)`).Error; err != nil {
		t.Fatalf("创建 backtest_job 唯一索引: %v", err)
	}
	return db
}

func newStrategySvc(t *testing.T) *StrategyService {
	t.Helper()
	db := strategyDB(t)
	repo := repository.NewStrategyRepository(db)
	return NewStrategyService(repo, NewBacktestService(repository.NewBacktestRepository(db), repo))
}

func mustNoErr(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func mustErrIs(t *testing.T, err, want error) {
	t.Helper()
	if !errors.Is(err, want) {
		t.Fatalf("err = %v, want %v", err, want)
	}
}

// seedActive 预置一个 active 策略（模拟 init.sql 种子；走合法状态机路径）
func seedActive(t *testing.T, svc *StrategyService, name string) {
	t.Helper()
	_, err := svc.Create(StrategyInput{
		Name:          name,
		ZhName:        "多因子轮动",
		FactorWeights: json.RawMessage(`{"ma_trend":0.2,"pe_ratio":0.1}`),
		Params:        json.RawMessage(`{"top_n":20}`),
	})
	mustNoErr(t, err)
	for _, s := range []string{model.StrategyBacktest, model.StrategySample, model.StrategyActive} {
		_, err = svc.Switch(name, s)
		mustNoErr(t, err)
	}
}

func TestStrategyCreateAndEdit(t *testing.T) {
	svc := newStrategySvc(t)
	st, err := svc.Create(StrategyInput{
		Name:          "mf_v2",
		ZhName:        "多因子轮动候选",
		FactorWeights: json.RawMessage(`{"ma_trend":0.2,"pe_ratio":0.1}`),
		Params:        json.RawMessage(`{"top_n":25}`),
	})
	mustNoErr(t, err)
	if st.Status != model.StrategyDraft || st.Version != "v1.0" {
		t.Errorf("新建应为 draft/v1.0, got %s/%s", st.Status, st.Version)
	}

	// 草稿可编辑
	upd, err := svc.Update("mf_v2", StrategyInput{Params: json.RawMessage(`{"top_n":30,"buy_buffer":10}`)})
	mustNoErr(t, err)
	var params struct {
		TopN int `json:"top_n"`
	}
	if err := json.Unmarshal(upd.Params, &params); err != nil || params.TopN != 30 {
		t.Errorf("更新后 top_n = %v, want 30", params.TopN)
	}

	// 非草稿不可编辑
	_, err = svc.Switch("mf_v2", model.StrategyBacktest)
	mustNoErr(t, err)
	_, err = svc.Update("mf_v2", StrategyInput{Params: json.RawMessage(`{"top_n":40}`)})
	mustErrIs(t, err, ErrStrategyNotDraft)

	// 重复 name 拒绝
	_, err = svc.Create(StrategyInput{Name: "mf_v2"})
	mustErrIs(t, err, ErrStrategyExists)

	// 空权重拒绝
	_, err = svc.Create(StrategyInput{Name: "bad", FactorWeights: json.RawMessage(`{}`)})
	mustErrIs(t, err, ErrStrategyInvalidData)
}

func TestStrategyStateMachineAndSingleActive(t *testing.T) {
	svc := newStrategySvc(t)
	seedActive(t, svc, "multi_factor")

	active, err := svc.repo.GetActive()
	mustNoErr(t, err)
	if active.Name != "multi_factor" {
		t.Errorf("active = %s, want multi_factor", active.Name)
	}

	// fork 新候选 → draft，version 递增
	forked, err := svc.Fork("multi_factor")
	mustNoErr(t, err)
	if forked.Name != "multi_factor_v2" || forked.Status != model.StrategyDraft || forked.Version != "v2.0" {
		t.Errorf("fork 异常: %+v", forked)
	}

	// 非法直跳（draft → active 不允许）
	_, err = svc.Switch("multi_factor_v2", model.StrategyActive)
	mustErrIs(t, err, ErrStrategyTransition)

	// 合法推进 draft → backtest → sample → active
	for _, s := range []string{model.StrategyBacktest, model.StrategySample, model.StrategyActive} {
		_, err = svc.Switch("multi_factor_v2", s)
		mustNoErr(t, err)
	}

	// 单 active：旧 active 降级 paused
	active, err = svc.repo.GetActive()
	mustNoErr(t, err)
	if active.Name != "multi_factor_v2" {
		t.Errorf("active = %s, want multi_factor_v2", active.Name)
	}
	old, err := svc.Get("multi_factor")
	mustNoErr(t, err)
	if old.Status != model.StrategyPaused {
		t.Errorf("旧 active 应降级 paused, got %s", old.Status)
	}

	// active 不可直接 archived
	_, err = svc.Switch("multi_factor_v2", model.StrategyArchived)
	mustErrIs(t, err, ErrStrategyTransition)

	// paused → archived；archived 冻结
	_, err = svc.Switch("multi_factor_v2", model.StrategyPaused)
	mustNoErr(t, err)
	_, err = svc.Switch("multi_factor_v2", model.StrategyArchived)
	mustNoErr(t, err)
	_, err = svc.Switch("multi_factor_v2", model.StrategyActive)
	mustErrIs(t, err, ErrStrategyTransition)
	_, err = svc.Fork("multi_factor_v2")
	mustErrIs(t, err, ErrStrategyArchived)

	// 不存在 → ErrStrategyNotFound
	_, err = svc.Get("nope")
	mustErrIs(t, err, ErrStrategyNotFound)
}

// 第一轮测试 #4：已暂停的策略可重新启用（paused → active 恢复运行，旧 active 降级 paused）
func TestStrategyPausedCanReactivate(t *testing.T) {
	svc := newStrategySvc(t)
	seedActive(t, svc, "multi_factor")

	// 暂停 multi_factor 后重新启用
	if _, err := svc.Switch("multi_factor", model.StrategyPaused); err != nil {
		t.Fatalf("暂停失败: %v", err)
	}
	st, err := svc.Switch("multi_factor", model.StrategyActive)
	mustNoErr(t, err)
	if st.Status != model.StrategyActive {
		t.Errorf("重新启用后应为 active, got %s", st.Status)
	}
	active, err := svc.repo.GetActive()
	mustNoErr(t, err)
	if active == nil || active.Name != "multi_factor" {
		t.Errorf("GetActive = %+v, want multi_factor", active)
	}
}

// 第一轮测试 #2：删除策略（草稿可删；运行中拒绝；已有信号记录拒绝）
func TestStrategyDelete(t *testing.T) {
	svc := newStrategySvc(t)
	mustErrIs(t, svc.Delete("不存在"), ErrStrategyNotFound)

	// 草稿可删除
	_, err := svc.Create(StrategyInput{
		Name: "tmp_draft", FactorWeights: json.RawMessage(`{"ma_trend":0.2}`),
	})
	mustNoErr(t, err)
	mustNoErr(t, svc.Delete("tmp_draft"))
	if _, err := svc.Get("tmp_draft"); !errors.Is(err, ErrStrategyNotFound) {
		t.Errorf("删除后应不存在, got err=%v", err)
	}

	// 运行中不可删
	seedActive(t, svc, "multi_factor")
	mustErrIs(t, svc.Delete("multi_factor"), ErrStrategyActiveNotDel)

	// 已有信号记录不可删（strategy_signal FK + 历史留痕）
	db := strategyDB(t)
	repo := repository.NewStrategyRepository(db)
	svc2 := NewStrategyService(repo, NewBacktestService(repository.NewBacktestRepository(db), repo))
	_, err = svc2.Create(StrategyInput{
		Name: "sig_strategy", FactorWeights: json.RawMessage(`{"ma_trend":0.2}`),
	})
	mustNoErr(t, err)
	// 直接向 strategy_signal 插入一条信号（模拟曾上线产生过信号）
	if err := db.Exec(
		`INSERT INTO strategy_signal (strategy_name, code, trade_date, score, action)
		 VALUES (?, '600000', CURRENT_DATE, 60.0, 'BUY')`,
		"sig_strategy",
	).Error; err != nil {
		t.Fatalf("插入信号失败: %v", err)
	}
	mustErrIs(t, svc2.Delete("sig_strategy"), ErrStrategyHasSignals)
}

func TestStrategyListLatestBacktestID(t *testing.T) {
	db := strategyDB(t)
	repo := repository.NewStrategyRepository(db)
	svc := NewStrategyService(repo, NewBacktestService(repository.NewBacktestRepository(db), repo))
	_, err := svc.Create(StrategyInput{Name: "mf_a", FactorWeights: json.RawMessage(`{"ma_trend":0.2}`)})
	mustNoErr(t, err)

	start := parseTestDate("2019-01-01")
	end := parseTestDate("2020-01-01")
	err = db.Create(&model.BacktestJob{
		StrategyName: "mf_a", StartDate: start, EndDate: end,
		TopN: 20, FillMode: "t1_open",
	}).Error
	mustNoErr(t, err)

	items, err := svc.List()
	mustNoErr(t, err)
	found := false
	for _, it := range items {
		if it.Name == "mf_a" && it.LatestBacktestID > 0 {
			found = true
		}
	}
	if !found {
		t.Error("latest_backtest_id 应返回 mf_a 的回测任务 id")
	}
}

func parseTestDate(s string) (t time.Time) {
	t, _ = time.Parse("2006-01-02", s)
	return
}

// seedDoneJob 把 job 置为 done 并写入结果（模拟引擎落库，供 Compare 组装 DTO）
func seedDoneJob(t *testing.T, db *gorm.DB, jobID uint64, turnover, cost float64, nav string) {
	t.Helper()
	if err := db.Model(&model.BacktestJob{}).Where("id = ?", jobID).
		Update("status", "done").Error; err != nil {
		t.Fatalf("置 done: %v", err)
	}
	if err := db.Create(&model.BacktestResult{
		JobID: jobID, FillMode: "t1_open",
		TotalReturn: 0.382, AnnualizedReturn: 0.051, MaxDrawdown: -0.214,
		Sharpe: 0.42, Turnover: turnover, Cost: cost, Trades: 120,
		Nav: nav, CreatedAt: time.Now(),
	}).Error; err != nil {
		t.Fatalf("seed backtest_result: %v", err)
	}
}

// TestCreateJobStrategyResolution §3.3/§4.3：strategy_name 空=active、提供=校验存在非归档
func TestCreateJobStrategyResolution(t *testing.T) {
	db := strategyDB(t)
	repo := repository.NewStrategyRepository(db)
	btSvc := NewBacktestService(repository.NewBacktestRepository(db), repo)
	start := parseTestDate("2019-01-01")
	end := parseTestDate("2020-01-01")

	// 无 active 且未指定 → ErrBacktestNoActive
	_, err := btSvc.CreateJob(start, end, 20, "t_close", "")
	mustErrIs(t, err, ErrBacktestNoActive)
	// 指定不存在的策略 → ErrStrategyNotFound
	_, err = btSvc.CreateJob(start, end, 20, "t_close", "nope")
	mustErrIs(t, err, ErrStrategyNotFound)

	// 激活 multi_factor → 空名解析为 active
	svc := NewStrategyService(repo, btSvc)
	seedActive(t, svc, "multi_factor")
	j, err := btSvc.CreateJob(start, end, 20, "t_close", "")
	mustNoErr(t, err)
	if j.StrategyName != "multi_factor" {
		t.Errorf("空 strategy_name 应解析为 active，got %s", j.StrategyName)
	}
	// 指定有效策略名透传
	j, err = btSvc.CreateJob(start, end, 20, "t_close", "multi_factor")
	mustNoErr(t, err)
	if j.StrategyName != "multi_factor" {
		t.Errorf("指定策略名应透传，got %s", j.StrategyName)
	}
	// 归档策略 → ErrStrategyArchived
	for _, s := range []string{model.StrategyPaused, model.StrategyArchived} {
		_, err = svc.Switch("multi_factor", s)
		mustNoErr(t, err)
	}
	_, err = btSvc.CreateJob(start, end, 20, "t_close", "multi_factor")
	mustErrIs(t, err, ErrStrategyArchived)
}

// TestStrategyCompare §3.3 A/B：同区间同假设建双 job，pending 轮询、done 组装两侧+基准
func TestStrategyCompare(t *testing.T) {
	db := strategyDB(t)
	repo := repository.NewStrategyRepository(db)
	btSvc := NewBacktestService(repository.NewBacktestRepository(db), repo)
	svc := NewStrategyService(repo, btSvc)

	for _, name := range []string{"multi_factor", "multi_factor_v2"} {
		_, err := svc.Create(StrategyInput{
			Name: name, FactorWeights: json.RawMessage(`{"ma_trend":0.2}`),
			Params: json.RawMessage(`{"top_n":20}`),
		})
		mustNoErr(t, err)
	}
	start := parseTestDate("2019-01-01")
	end := parseTestDate("2020-01-01")

	// 首次提交：job 均 pending → 返回 pending（前端轮询）
	out, err := svc.Compare("multi_factor", "multi_factor_v2", start, end, "t1_open")
	mustNoErr(t, err)
	if out.Status != "pending" {
		t.Errorf("新建 job 应为 pending，got %s", out.Status)
	}
	if out.Base == nil || out.Cand == nil || out.Base.ID == out.Cand.ID {
		t.Error("两侧应各建一个 job")
	}

	// 造完成结果 → 幂等复用 → done + 指标/净值/基准
	nav := `[{"date":"2019-01-02","nav":1.0,"benchmark":1.0},` +
		`{"date":"2019-01-03","nav":1.05,"benchmark":0.98}]`
	seedDoneJob(t, db, out.Base.ID, 8.6, 0.012, nav)
	seedDoneJob(t, db, out.Cand.ID, 9.2, 0.015, nav)

	out, err = svc.Compare("multi_factor", "multi_factor_v2", start, end, "t1_open")
	mustNoErr(t, err)
	if out.Status != "done" {
		t.Errorf("结果就绪应为 done，got %s", out.Status)
	}
	if out.Base.Result == nil || out.Base.Result.Turnover != 8.6 || out.Base.Result.Cost != 0.012 {
		t.Errorf("base 结果异常: %+v", out.Base.Result)
	}
	if out.Cand.Result == nil || out.Cand.Result.Turnover != 9.2 {
		t.Errorf("candidate 结果异常: %+v", out.Cand.Result)
	}

	// 不存在的策略 → ErrStrategyNotFound
	_, err = svc.Compare("multi_factor", "nope", start, end, "t1_open")
	mustErrIs(t, err, ErrStrategyNotFound)
}
