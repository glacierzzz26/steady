package service

import (
	"encoding/json"
	"math"
	"os"
	"strings"
	"testing"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"

	"quant-system/backend/internal/model"
	"quant-system/backend/internal/repository"
)

// factorStatsDB 因子统计集成测试库（TEST_DB_DSN 门控，模式对齐 strategyDB）：
//   - 未设 TEST_DB_DSN 时跳过
//   - 拒绝连接生产库 quant_system
//   - 先删后建 factor_definition/factor_stat/factor_corr，保证可重复执行
func factorStatsDB(t *testing.T) *gorm.DB {
	t.Helper()
	dsn := os.Getenv("TEST_DB_DSN")
	if dsn == "" {
		t.Skip("TEST_DB_DSN 未设置，跳过集成测试（本地/CI 设置后自动启用）")
	}
	if strings.Contains(dsn, "dbname=quant_system") && !strings.Contains(dsn, "dbname=quant_system_test") {
		t.Fatal("拒绝连接生产库 quant_system，请使用独立测试库 quant_system_test")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{
		NamingStrategy: schema.NamingStrategy{SingularTable: true},
	})
	if err != nil {
		t.Fatalf("连接测试库失败: %v", err)
	}
	if err := db.Migrator().DropTable(&model.FactorStat{}, &model.FactorCorr{}); err != nil {
		t.Fatalf("清理 factor_stat/factor_corr 失败: %v", err)
	}
	// factor_value 可能存在旧数据，仅 drop 定义/统计三表即可
	if err := db.Migrator().DropTable(&model.FactorDefinition{}); err != nil {
		t.Fatalf("清理 factor_definition 失败: %v", err)
	}
	if err := db.AutoMigrate(&model.FactorDefinition{}, &model.FactorStat{}, &model.FactorCorr{}); err != nil {
		t.Fatalf("AutoMigrate 失败: %v", err)
	}
	return db
}

func newFactorStatsSvc(t *testing.T) (*FactorStatsService, *gorm.DB) {
	t.Helper()
	db := factorStatsDB(t)
	return NewFactorStatsService(repository.NewFactorRepository(db)), db
}

func f64(v float64) *float64 { return &v }

// seedFactorDefs 预置因子定义（对应 init.sql 种子，status/version 显式）
func seedFactorDefs(t *testing.T, db *gorm.DB) {
	t.Helper()
	defs := []model.FactorDefinition{
		{Name: "ma_trend", Category: "trend", Weight: 0.3, Version: "v1.0", Status: "active"},
		{Name: "pe_ratio", Category: "value", Weight: 0.2, Version: "v1.0", Status: "active"},
	}
	for _, d := range defs {
		if err := db.Create(&d).Error; err != nil {
			t.Fatalf("seed factor_definition: %v", err)
		}
	}
}

// statDates 5 个已知交易日的 IC/Q 行（手算基准见 TestFactorStatsAggregation）
var statDates = []string{"2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"}

// seedFactorStat ma_trend：ic_5d=[0.1,0.2,0.3,0.4,0.5]（mean=0.3, std=√0.02）；
// ic_1d 恒 0.2、ic_60d 恒 0.05、ic_10d/ic_20d 为 NULL；q 恒 q1>q5，末日期翻转 → 胜率 4/5。
func seedFactorStat(t *testing.T, db *gorm.DB) {
	t.Helper()
	ics := []float64{0.1, 0.2, 0.3, 0.4, 0.5}
	for i, ds := range statDates {
		d := parseTestDate(ds)
		q1, q5 := 0.05, -0.03
		if i == 4 { // 末日期翻转：q1<q5（不算 Q1 胜）
			q1, q5 = -0.05, -0.01
		}
		row := model.FactorStat{
			FactorName: "ma_trend", TradeDate: d,
			IC1D: f64(0.2), IC5D: f64(ics[i]), // IC10D/IC20D 留 NULL
			IC60D: f64(0.05),
			Q1:    f64(q1), Q2: f64(0.03), Q3: f64(0.01), Q4: f64(-0.01), Q5: f64(q5),
		}
		if err := db.Create(&row).Error; err != nil {
			t.Fatalf("seed factor_stat %s: %v", ds, err)
		}
	}
}

// seedFactorCorr 两个交易日的 6×6 矩阵：ma×pe=[1.0, 0.5]、ma×macd 恒 0.5，其余格 NULL
// → 平均后 ma×pe=0.75、ma×macd=0.5、ma×ma=1.0。
func seedFactorCorr(t *testing.T, db *gorm.DB) {
	t.Helper()
	mk := func(maPe, maMacd float64) []byte {
		m := make([][]*float64, 6)
		for i := range m {
			m[i] = make([]*float64, 6)
		}
		m[0][0] = f64(1.0)
		m[0][1] = f64(maMacd)
		m[0][2] = f64(maPe)
		m[1][0] = f64(maMacd)
		m[2][0] = f64(maPe)
		b, _ := json.Marshal(m)
		return b
	}
	dates := []string{"2026-01-06", "2026-01-07"}
	vals := [][2]float64{{1.0, 0.5}, {0.5, 0.5}}
	for i, ds := range dates {
		row := model.FactorCorr{
			TradeDate: parseTestDate(ds),
			Matrix:    mk(vals[i][0], vals[i][1]),
		}
		if err := db.Create(&row).Error; err != nil {
			t.Fatalf("seed factor_corr %s: %v", ds, err)
		}
	}
}

func TestFactorStatsAggregation(t *testing.T) {
	svc, db := newFactorStatsSvc(t)
	seedFactorDefs(t, db)
	seedFactorStat(t, db)

	out, err := svc.Stats("ma_trend", parseTestDate("2026-01-01"), parseTestDate("2026-01-31"), 5)
	mustNoErr(t, err)

	if out.Factor != "ma_trend" || out.Category != "trend" || out.Horizon != 5 {
		t.Errorf("元数据异常: %+v", out)
	}
	if out.Range.Start != "2026-01-01" || out.Range.End != "2026-01-31" || out.Range.Days != 31 {
		t.Errorf("range 异常: %+v", out.Range)
	}

	// IC 序列：5 点非空，值精确
	if len(out.ICSeries) != 5 {
		t.Fatalf("ic_series 应 5 点，got %d", len(out.ICSeries))
	}
	for i, p := range out.ICSeries {
		if p.Date != statDates[i] {
			t.Errorf("点 %d 日期 = %s, want %s", i, p.Date, statDates[i])
		}
		if p.IC == nil || math.Abs(*p.IC-(0.1+0.1*float64(i))) > 1e-9 {
			t.Errorf("点 %d ic = %v, want %v", i, p.IC, 0.1+0.1*float64(i))
		}
	}

	// IC mean / std（总体 ddof=0）/ ICIR = 0.3/√0.02
	if out.ICMean == nil || math.Abs(*out.ICMean-0.3) > 1e-9 {
		t.Errorf("ic_mean = %v, want 0.3", out.ICMean)
	}
	if out.ICStd == nil || math.Abs(*out.ICStd-math.Sqrt(0.02)) > 1e-9 {
		t.Errorf("ic_std = %v, want √0.02", out.ICStd)
	}
	if out.ICIR == nil || math.Abs(*out.ICIR-0.3/math.Sqrt(0.02)) > 1e-6 {
		t.Errorf("icir = %v, want 0.3/√0.02", out.ICIR)
	}

	// IC 衰减：h1=0.2、h5=0.3、h10/h20=NULL、h60=0.05
	wantDecay := map[int]float64{1: 0.2, 5: 0.3, 60: 0.05}
	if len(out.ICDecay) != 5 {
		t.Fatalf("ic_decay 应 5 档，got %d", len(out.ICDecay))
	}
	for _, d := range out.ICDecay {
		if w, ok := wantDecay[d.Horizon]; ok {
			if d.IC == nil || math.Abs(*d.IC-w) > 1e-9 {
				t.Errorf("decay h%d = %v, want %v", d.Horizon, d.IC, w)
			}
		} else if d.IC != nil {
			t.Errorf("decay h%d 应 NULL，got %v", d.Horizon, *d.IC)
		}
	}

	// 分层：q1 跨日均值 = (0.05*4 + (-0.05))/5 = 0.03；q5 = (-0.03*4 + (-0.01))/5 = -0.026
	if len(out.Quantiles) != 5 {
		t.Fatalf("quantiles 应 5 组，got %d", len(out.Quantiles))
	}
	if out.Quantiles[0].Ret == nil || math.Abs(*out.Quantiles[0].Ret-0.03) > 1e-9 {
		t.Errorf("q1 mean = %v, want 0.03", out.Quantiles[0].Ret)
	}
	if out.Quantiles[4].Ret == nil || math.Abs(*out.Quantiles[4].Ret+0.026) > 1e-9 {
		t.Errorf("q5 mean = %v, want -0.026", out.Quantiles[4].Ret)
	}

	// 单调性：5 日 4 日 Q1>Q5 → 0.8
	if out.Monotonic == nil || math.Abs(*out.Monotonic-0.8) > 1e-9 {
		t.Errorf("monotonic = %v, want 0.8", out.Monotonic)
	}
}

// 数据诚实：因子有定义但无统计行 → 空序列 + nil 聚合（前端渲染空态，不编造数字）
func TestFactorStatsEmptyState(t *testing.T) {
	svc, db := newFactorStatsSvc(t)
	seedFactorDefs(t, db)

	out, err := svc.Stats("pe_ratio", parseTestDate("2026-01-01"), parseTestDate("2026-01-31"), 5)
	mustNoErr(t, err)
	if len(out.ICSeries) != 0 {
		t.Errorf("无数据因子 ic_series 应为空，got %d 点", len(out.ICSeries))
	}
	if out.ICMean != nil || out.ICIR != nil || out.Monotonic != nil {
		t.Errorf("无数据因子聚合应 nil，got mean=%v icir=%v mono=%v",
			out.ICMean, out.ICIR, out.Monotonic)
	}
}

func TestFactorStatsValidation(t *testing.T) {
	svc, db := newFactorStatsSvc(t)
	seedFactorDefs(t, db)
	start, end := parseTestDate("2026-01-01"), parseTestDate("2026-01-31")

	_, err := svc.Stats("nope", start, end, 5)
	mustErrIs(t, err, ErrFactorNotFound)
	_, err = svc.Stats("ma_trend", start, end, 7)
	mustErrIs(t, err, ErrFactorHorizon)
	_, err = svc.Stats("ma_trend", end, start, 5) // start 不早于 end
	mustErrIs(t, err, ErrFactorRange)
	_, err = svc.Stats("ma_trend", parseTestDate("2010-01-01"), parseTestDate("2020-01-01"), 5)
	mustErrIs(t, err, ErrFactorSpan)
	_, err = svc.Correlation(end, start)
	mustErrIs(t, err, ErrFactorRange)
	_, err = svc.Correlation(parseTestDate("2010-01-01"), parseTestDate("2020-01-01"))
	mustErrIs(t, err, ErrFactorSpan)
}

func TestFactorCorrelationAggregation(t *testing.T) {
	svc, db := newFactorStatsSvc(t)
	seedFactorCorr(t, db)

	out, err := svc.Correlation(parseTestDate("2026-01-01"), parseTestDate("2026-01-31"))
	mustNoErr(t, err)

	// 6 因子规范序（与 quant-engine CORR_FACTORS 一致）
	if len(out.Factors) != 6 || out.Factors[0] != "ma_trend" || out.Factors[2] != "pe_ratio" {
		t.Errorf("factors = %v", out.Factors)
	}
	if len(out.Matrix) != 6 {
		t.Fatalf("matrix 应 6×6")
	}
	// 平均：ma×ma=1.0（2 日恒）、ma×macd=0.5、ma×pe=(1+0.5)/2=0.75
	if out.Matrix[0][0] == nil || math.Abs(*out.Matrix[0][0]-1.0) > 1e-9 {
		t.Errorf("ma×ma = %v, want 1.0", out.Matrix[0][0])
	}
	if out.Matrix[0][1] == nil || math.Abs(*out.Matrix[0][1]-0.5) > 1e-9 {
		t.Errorf("ma×macd = %v, want 0.5", out.Matrix[0][1])
	}
	if out.Matrix[0][2] == nil || math.Abs(*out.Matrix[0][2]-0.75) > 1e-9 {
		t.Errorf("ma×pe = %v, want 0.75", out.Matrix[0][2])
	}
	// 从未共现的格（如 macd×pe）保持 NULL
	if out.Matrix[1][2] != nil {
		t.Errorf("macd×pe 应 NULL，got %v", out.Matrix[1][2])
	}
	// 对称性
	if out.Matrix[2][0] == nil || math.Abs(*out.Matrix[2][0]-0.75) > 1e-9 {
		t.Errorf("pe×ma = %v, want 0.75", out.Matrix[2][0])
	}
}

func TestFactorStatsICIRGuard(t *testing.T) {
	svc, db := newFactorStatsSvc(t)
	seedFactorDefs(t, db)
	// 仅 1 个交易日（样本 <5）→ ICIR 为 nil，均值照常
	row := model.FactorStat{
		FactorName: "ma_trend", TradeDate: parseTestDate("2026-01-05"),
		IC1D: f64(0.1), IC5D: f64(0.1),
	}
	if err := db.Create(&row).Error; err != nil {
		t.Fatalf("seed: %v", err)
	}
	out, err := svc.Stats("ma_trend", parseTestDate("2026-01-01"), parseTestDate("2026-01-31"), 5)
	mustNoErr(t, err)
	if out.ICIR != nil {
		t.Errorf("样本不足时 icir 应为 nil，got %v", *out.ICIR)
	}
	if out.ICMean == nil || math.Abs(*out.ICMean-0.1) > 1e-9 {
		t.Errorf("ic_mean = %v, want 0.1", out.ICMean)
	}
}
