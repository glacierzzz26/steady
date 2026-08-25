package service

import (
	"encoding/json"
	"errors"
	"math"
	"time"

	"gorm.io/gorm"

	"quant-system/backend/internal/model"
	"quant-system/backend/internal/repository"
)

// 因子检验统计服务（2.3 G9）：读 factor_stat/factor_corr 预计算表 + 轻聚合
// IC 数学全部在 quant-engine（Python/pandas）单实现，Go 只做 mean/std/比值/矩阵平均，
// 避免两套 IC 实现漂移（设计定稿《因子研究闭环》§4.2）。

// 校验错误
var (
	ErrFactorNotFound = errors.New("因子不存在")
	ErrFactorHorizon  = errors.New("horizon 仅支持 1/5/10/20/60")
	ErrFactorRange    = errors.New("起始日不能晚于结束日")
	ErrFactorSpan     = errors.New("统计区间不能超过5年")
)

// maxFactorSpanYears 区间上限（与回测同纪律）
const maxFactorSpanYears = 5

// corrFactors 相关性矩阵固定因子序（与 quant-engine CORR_FACTORS 一致）
var corrFactors = []string{"ma_trend", "macd_signal", "pe_ratio", "pb_ratio",
	"roe_quality", "debt_risk"}

// factorHorizonCols IC 衰减固定档位（对齐 quant-engine HORIZONS）
var factorHorizonCols = []int{1, 5, 10, 20, 60}

// validHorizon horizon 是否在档位集合内（0 值 FactorStat 的列指针为 nil，
// 不能靠「取列是否 nil」判断，须显式查档位）
func validHorizon(h int) bool {
	for _, v := range factorHorizonCols {
		if v == h {
			return true
		}
	}
	return false
}

// FactorStatsService 因子检验统计服务
type FactorStatsService struct {
	repo *repository.FactorRepository
}

func NewFactorStatsService(repo *repository.FactorRepository) *FactorStatsService {
	return &FactorStatsService{repo: repo}
}

// ---- 响应 DTO（对齐契约 §6.1） ----

// FactorRange 统计区间
type FactorRange struct {
	Start string `json:"start"`
	End   string `json:"end"`
	Days  int    `json:"days"`
}

// FactorICPoint RankIC 时序点（所选 horizon 的当日 IC；尾部滞后为 null）
type FactorICPoint struct {
	Date string   `json:"date"`
	IC   *float64 `json:"ic"`
}

// FactorDecayPoint IC 衰减点（某 horizon 的区间均值）
type FactorDecayPoint struct {
	Horizon int      `json:"horizon"`
	IC      *float64 `json:"ic"`
}

// FactorQuantile 分层点（组均前向收益，Q1=因子最优组）
type FactorQuantile struct {
	Group int      `json:"group"`
	Ret   *float64 `json:"ret"`
}

// FactorStatsResult 因子检验统计（一次给齐 FactorLab 页）
type FactorStatsResult struct {
	Factor    string             `json:"factor"`
	Category  string             `json:"category"`
	Range     FactorRange        `json:"range"`
	Horizon   int                `json:"horizon"`
	ICSeries  []FactorICPoint    `json:"ic_series"`
	ICIR      *float64           `json:"icir"`
	ICMean    *float64           `json:"ic_mean"`
	ICStd     *float64           `json:"ic_std"`
	ICDecay   []FactorDecayPoint `json:"ic_decay"`
	Quantiles []FactorQuantile   `json:"quantiles"`
	Monotonic *float64           `json:"monotonic"`
}

// FactorCorrResult 6 因子相关矩阵（区间内 per-date 矩阵逐格平均，无共现格为 null）
type FactorCorrResult struct {
	Factors []string     `json:"factors"`
	Matrix  [][]*float64 `json:"matrix"`
}

// ---- 列选择与聚合（与 quant-engine 同口径） ----

// factorStatIC 取某 horizon 对应的 IC 列（*float64：NULL → nil）
func factorStatIC(r *model.FactorStat, h int) *float64 {
	switch h {
	case 1:
		return r.IC1D
	case 5:
		return r.IC5D
	case 10:
		return r.IC10D
	case 20:
		return r.IC20D
	case 60:
		return r.IC60D
	}
	return nil
}

func factorStatQ(r *model.FactorStat, g int) *float64 {
	switch g {
	case 1:
		return r.Q1
	case 2:
		return r.Q2
	case 3:
		return r.Q3
	case 4:
		return r.Q4
	case 5:
		return r.Q5
	}
	return nil
}

// meanStdPop 总体均值/标准差（ddof=0，与 quant-engine icir 定义一致）；跳过 nil
func meanStdPop(vals []*float64) (mean, std float64, n int) {
	for _, v := range vals {
		if v != nil {
			mean += *v
			n++
		}
	}
	if n == 0 {
		return 0, 0, 0
	}
	mean /= float64(n)
	var sq float64
	for _, v := range vals {
		if v != nil {
			d := *v - mean
			sq += d * d
		}
	}
	return mean, math.Sqrt(sq / float64(n)), n
}

func meanOf(vals []*float64) *float64 {
	mean, _, n := meanStdPop(vals)
	if n == 0 {
		return nil
	}
	return &mean
}

func ptr(v float64) *float64 { return &v }

// ---- 服务方法 ----

// Stats 因子检验统计：IC 时序/ICIR/衰减/分层/单调性，一次返回
func (s *FactorStatsService) Stats(name string, start, end time.Time, horizon int) (*FactorStatsResult, error) {
	def, err := s.repo.GetFactor(name)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, ErrFactorNotFound
	}
	if err != nil {
		return nil, err
	}
	if !validHorizon(horizon) {
		return nil, ErrFactorHorizon
	}
	if !start.Before(end) {
		return nil, ErrFactorRange
	}
	if end.Sub(start) > maxFactorSpanYears*365*24*time.Hour {
		return nil, ErrFactorSpan
	}

	rows, err := s.repo.GetStat(name, start, end)
	if err != nil {
		return nil, err
	}

	// 主 IC 序列（所选 horizon），同时收集非空值做均值/ICIR
	series := make([]FactorICPoint, 0, len(rows))
	icVals := make([]*float64, 0, len(rows))
	for i := range rows {
		v := factorStatIC(&rows[i], horizon)
		icVals = append(icVals, v)
		series = append(series, FactorICPoint{
			Date: rows[i].TradeDate.Format("2006-01-02"), IC: v,
		})
	}

	mean, std, n := meanStdPop(icVals)
	var icMean, icStd, icir *float64
	if n > 0 {
		icMean, icStd = &mean, &std
	}
	if n >= 5 && std > 0 {
		icir = ptr(mean / std)
	}

	// IC 衰减：各档位区间均值
	decay := make([]FactorDecayPoint, 0, len(factorHorizonCols))
	for _, h := range factorHorizonCols {
		vals := make([]*float64, 0, len(rows))
		for i := range rows {
			vals = append(vals, factorStatIC(&rows[i], h))
		}
		decay = append(decay, FactorDecayPoint{Horizon: h, IC: meanOf(vals)})
	}

	// 分层：跨日均值 q1..q5；单调性 = Q1 跑赢 Q5 的交易日占比（胜率）
	quantiles := make([]FactorQuantile, 0, 5)
	for g := 1; g <= 5; g++ {
		vals := make([]*float64, 0, len(rows))
		for i := range rows {
			vals = append(vals, factorStatQ(&rows[i], g))
		}
		quantiles = append(quantiles, FactorQuantile{Group: g, Ret: meanOf(vals)})
	}
	wins, total := 0, 0
	for i := range rows {
		if rows[i].Q1 != nil && rows[i].Q5 != nil {
			total++
			if *rows[i].Q1 > *rows[i].Q5 {
				wins++
			}
		}
	}
	var monotonic *float64
	if total > 0 {
		monotonic = ptr(float64(wins) / float64(total))
	}

	return &FactorStatsResult{
		Factor:    def.Name,
		Category:  def.Category,
		Range:     FactorRange{start.Format("2006-01-02"), end.Format("2006-01-02"), int(end.Sub(start).Hours()/24) + 1},
		Horizon:   horizon,
		ICSeries:  series,
		ICIR:      icir,
		ICMean:    icMean,
		ICStd:     icStd,
		ICDecay:   decay,
		Quantiles: quantiles,
		Monotonic: monotonic,
	}, nil
}

// Correlation 6 因子相关矩阵（per-date 矩阵逐格平均）
func (s *FactorStatsService) Correlation(start, end time.Time) (*FactorCorrResult, error) {
	if !start.Before(end) {
		return nil, ErrFactorRange
	}
	if end.Sub(start) > maxFactorSpanYears*365*24*time.Hour {
		return nil, ErrFactorSpan
	}
	rows, err := s.repo.GetCorr(start, end)
	if err != nil {
		return nil, err
	}
	n := len(corrFactors)
	sums := make([][]float64, n)
	cnts := make([][]int, n)
	for i := range sums {
		sums[i] = make([]float64, n)
		cnts[i] = make([]int, n)
	}
	for _, r := range rows {
		var mat [][]*float64
		if err := json.Unmarshal(r.Matrix, &mat); err != nil {
			continue // 脏行跳过，不影响其余日期
		}
		if len(mat) != n {
			continue
		}
		for i := 0; i < n; i++ {
			if len(mat[i]) != n {
				continue
			}
			for j := 0; j < n; j++ {
				if mat[i][j] != nil {
					sums[i][j] += *mat[i][j]
					cnts[i][j]++
				}
			}
		}
	}
	matrix := make([][]*float64, n)
	for i := range matrix {
		matrix[i] = make([]*float64, n)
		for j := range matrix[i] {
			if cnts[i][j] > 0 {
				matrix[i][j] = ptr(sums[i][j] / float64(cnts[i][j]))
			}
		}
	}
	return &FactorCorrResult{Factors: corrFactors, Matrix: matrix}, nil
}
