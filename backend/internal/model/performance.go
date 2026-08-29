package model

import "time"

// StrategyPerf 策略效果度量预计算结果（方向① 第一期：quant-engine 预计算 / 本服务只读）
//
// 对应表 strategy_perf（迁移 006 / init.sql 5.4 段）。
// metric_type：hit_rate（BUY 样本 forward 5/10/20d 收益 + 相对基准命中）/
// nav_overlay（实盘 account_nav vs 回测 t1_open nav vs 基准 sh000300 归一叠加）。
// detail 为 JSONB，原始 JSON 字符串，由 handler 解析为结构化响应。
type StrategyPerf struct {
	ID           uint64    `gorm:"primaryKey" json:"id"`
	StrategyName string    `gorm:"size:50;not null" json:"strategy_name"`
	PeriodStart  time.Time `gorm:"type:date;not null" json:"period_start"`
	PeriodEnd    time.Time `gorm:"type:date;not null" json:"period_end"`
	MetricType   string    `gorm:"size:20;not null" json:"metric_type"`
	Detail       string    `gorm:"type:jsonb" json:"detail"` // 原始 JSON 字符串，handler 转结构体
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}

func (StrategyPerf) TableName() string { return "strategy_perf" }
