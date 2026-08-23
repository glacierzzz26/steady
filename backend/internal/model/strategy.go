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
type FactorDefinition struct {
	ID          uint64    `gorm:"primaryKey"`
	Name        string    `gorm:"uniqueIndex;size:50;not null"`
	Category    string    `gorm:"size:20"` // trend / value / quality / risk
	Description string    `gorm:"type:text"`
	Formula     string    `gorm:"type:text"`
	Weight      float64   `gorm:"type:decimal(5,4)"`
	CreatedAt   time.Time
}

func (FactorDefinition) TableName() string { return "factor_definition" }

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
