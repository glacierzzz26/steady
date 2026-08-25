package model

import (
	"time"

	"gorm.io/datatypes"
)

// 信号动作
const (
	ActionBuy  = "BUY"
	ActionSell = "SELL"
	ActionHold = "HOLD"
)

// FactorDefinition 因子定义（对应表 factor_definition）
// 2.3 因子研究：version 版本号、status 状态机（draft/trial/verified/active/disabled，
// 仅 active 参与评分池）。DDL 以 init.sql/002 迁移为准。
type FactorDefinition struct {
	ID          uint64    `gorm:"primaryKey"`
	Name        string    `gorm:"uniqueIndex;size:50;not null"`
	Category    string    `gorm:"size:20"` // trend / value / quality / risk
	Description string    `gorm:"type:text"`
	Formula     string    `gorm:"type:text"`
	Weight      float64   `gorm:"type:decimal(5,4)"`
	Version     string    `gorm:"size:20;default:v1.0"`
	Status      string    `gorm:"size:10;default:active"`
	CreatedAt   time.Time
}

func (FactorDefinition) TableName() string { return "factor_definition" }

// FactorStat 因子检验统计（G9 FactorLab 数据源；quant-engine 预计算 / Go 读取聚合）
// per-date 追加：每 (因子, 交易日) 一行；IC 系列为 T→T+{1,5,10,20,60} 横截面 Rank IC，
// q1..q5 为当日 5 分层组均前向收益（H=5，q1=因子最优组）。DDL 见 init.sql/002 迁移。
// IC/Q 列可空（尾部滞后/样本不足 → NULL），用 *float64 区分 NULL 与真 0，避免均值被污染。
// 注意：IC/Q 列必须显式 column tag——生产 schema（init.sql + 002 迁移）为 ic_1d..ic_60d，
// GORM 默认命名 IC5D→ic5_d 会漂移（e2e 抓到的真实 bug），此处全部对齐 SQL 列名。
type FactorStat struct {
	ID         uint64    `gorm:"primaryKey"`
	FactorName string    `gorm:"size:50;not null"`
	TradeDate  time.Time `gorm:"type:date;not null"`
	IC1D       *float64  `gorm:"column:ic_1d;type:decimal(12,6)"`
	IC5D       *float64  `gorm:"column:ic_5d;type:decimal(12,6)"`
	IC10D      *float64  `gorm:"column:ic_10d;type:decimal(12,6)"`
	IC20D      *float64  `gorm:"column:ic_20d;type:decimal(12,6)"`
	IC60D      *float64  `gorm:"column:ic_60d;type:decimal(12,6)"`
	Q1         *float64  `gorm:"column:q1;type:decimal(12,6)"`
	Q2         *float64  `gorm:"column:q2;type:decimal(12,6)"`
	Q3         *float64  `gorm:"column:q3;type:decimal(12,6)"`
	Q4         *float64  `gorm:"column:q4;type:decimal(12,6)"`
	Q5         *float64  `gorm:"column:q5;type:decimal(12,6)"`
}

func (FactorStat) TableName() string { return "factor_stat" }

// FactorCorr 因子两两相关矩阵（6×6，per-date，Go 读区间做矩阵平均）
// Matrix 为 JSONB 6×6 数组，行/列序固定为 6 因子规范序（见 service/factor_stats.go）。
type FactorCorr struct {
	ID        uint64         `gorm:"primaryKey"`
	TradeDate time.Time      `gorm:"type:date;not null"`
	Matrix    datatypes.JSON `gorm:"column:matrix;type:jsonb"`
}

func (FactorCorr) TableName() string { return "factor_corr" }

// FactorValue 因子值（对应表 factor_value，横截面排名）
type FactorValue struct {
	ID         uint64    `gorm:"primaryKey"`
	Code       string    `gorm:"size:10;not null"`
	FactorName string    `gorm:"size:50;not null"`
	TradeDate  time.Time `gorm:"type:date;not null"`
	Value      float64   `gorm:"type:decimal(15,6)"`
	Rank       int       // 因子排名
	Normalized float64   `gorm:"type:decimal(8,6)"` // 归一化值（0-1）
}

func (FactorValue) TableName() string { return "factor_value" }

// 策略状态（Iteration 4 状态机：draft → backtest → sample → active → paused/archived）
const (
	StrategyDraft     = "draft"     // 草稿：可编辑
	StrategyBacktest  = "backtest"  // 回测验证
	StrategySample    = "sample"    // 样本外验证
	StrategyActive    = "active"    // 运行中（唯一，驱动每日信号）
	StrategyPaused    = "paused"    // 已暂停
	StrategyArchived  = "archived"  // 已归档（冻结，不可编辑不可激活）
)

// Strategy 策略（对应表 strategy，factor_weights/params 为 JSONB）
//
// Iteration 4 语义：
// - factor_weights 存**因子级权重映射**（合计 1.0，如 {"ma_trend":0.2,...}），
//   评分/回测/排名三处同源；factor_definition.weight 降级为新建默认值来源。
// - 每个版本一条独立记录（name 唯一）；version 列仅作展示/排序，
//   版本化通过 fork 生成新 name（如 multi_factor_v2）。
// - 同一时间仅一个 status='active' 的策略（服务层强制）。
type Strategy struct {
	ID            uint64         `gorm:"primaryKey"`
	// 用 `unique` 而非 `uniqueIndex`：GORM 的 MigrateColumnUnique 判断列唯一性用的是
	// DB 实际列（生产 strategy.name 有唯一约束 → columnType.Unique()=true），若模型只写 uniqueIndex
	// 而 field.Unique=false，GORM 会按默认名 uni_strategy_name 去 DROP 约束 → 42704（生产已踩）。
	// `unique` 与 DB 现状对齐后两个分支都跳过，不增删任何约束/索引，升级/新装均安全。
	Name          string         `gorm:"unique;size:50;not null"`
	ZhName        string         `gorm:"size:50"` // 中文名（前端展示）
	Description   string         `gorm:"type:text"`
	Version       string         `gorm:"size:20;default:v1.0"`
	FactorWeights datatypes.JSON `gorm:"type:jsonb"`
	Params        datatypes.JSON `gorm:"type:jsonb"`
	Status        string         `gorm:"size:10;default:draft"`
	CreatedAt     time.Time
	UpdatedAt     time.Time
}

func (Strategy) TableName() string { return "strategy" }

// StrategySignal 策略信号（对应表 strategy_signal）
type StrategySignal struct {
	ID           uint64    `gorm:"primaryKey"`
	StrategyName string    `gorm:"size:50;not null"`
	Code         string    `gorm:"size:10;not null"`
	TradeDate    time.Time `gorm:"type:date;not null"`
	Score        float64   `gorm:"type:decimal(8,4)"` // 综合评分（0-100）
	Action       string    `gorm:"size:10"`           // BUY / SELL / HOLD
	Reason       string    `gorm:"type:text"`
	CreatedAt    time.Time
}

func (StrategySignal) TableName() string { return "strategy_signal" }
