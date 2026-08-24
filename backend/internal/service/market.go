package service

import (
	"database/sql"
	"time"

	"gorm.io/gorm"
)

// ChinaTZ 中国标准时间（Asia/Shanghai，无夏令时）。容器已设 TZ=Asia/Shanghai，
// 这里显式固定，避免部署环境差异导致开市判断偏移。
var ChinaTZ = time.FixedZone("CST", 8*3600)

// MarketStatus 市场状态（右上角「开市/休市」chip 与侧边栏交易日信息）。
// 数据源 trade_calendar（权威交易日历，collector 日度同步，含法定节假日）。
type MarketStatus struct {
	Today         string `json:"today"`           // 当前日期 YYYY-MM-DD（CST）
	IsTradeDay    bool   `json:"is_trade_day"`    // 今日是否为交易日
	MarketPhase   string `json:"market_phase"`    // pre_open / open / lunch_break / closed / off_day
	PhaseLabel    string `json:"phase_label"`     // 未开盘 / 交易中 / 午间休市 / 已收盘 / 休市
	LastTradeDate string `json:"last_trade_date"` // 上一交易日（< today）
	NextTradeDate string `json:"next_trade_date"` // 下一交易日（>= today，交易日当天=今日）
}

// MarketStatusService 只读市场状态服务（不对 trade_calendar 做写）。
type MarketStatusService struct {
	db *gorm.DB
}

func NewMarketStatusService(db *gorm.DB) *MarketStatusService {
	return &MarketStatusService{db: db}
}

// GetStatus 按给定时刻（通常 time.Now()）计算市场状态。
func (s *MarketStatusService) GetStatus(now time.Time) (*MarketStatus, error) {
	now = now.In(ChinaTZ)
	today := now.Format("2006-01-02")

	var lastT, nextT, todayT sql.NullTime
	if err := s.db.Raw(
		`SELECT MAX(cal_date) FROM trade_calendar WHERE is_open AND cal_date < ?`, today,
	).Scan(&lastT).Error; err != nil {
		return nil, err
	}
	if err := s.db.Raw(
		`SELECT MIN(cal_date) FROM trade_calendar WHERE is_open AND cal_date >= ?`, today,
	).Scan(&nextT).Error; err != nil {
		return nil, err
	}
	if err := s.db.Raw(
		`SELECT cal_date FROM trade_calendar WHERE is_open AND cal_date = ?`, today,
	).Scan(&todayT).Error; err != nil {
		return nil, err
	}

	st := &MarketStatus{
		Today:         today,
		IsTradeDay:    todayT.Valid,
		MarketPhase:   "off_day",
		PhaseLabel:    "休市",
		LastTradeDate: fmtNullDate(lastT),
		NextTradeDate: fmtNullDate(nextT),
	}
	if !st.IsTradeDay {
		return st, nil
	}

	// 交易日：按钟点判定盘中/休市（A股 09:30-11:30 / 13:00-15:00）
	hm := now.Hour()*100 + now.Minute()
	switch {
	case hm < 930:
		st.MarketPhase, st.PhaseLabel = "pre_open", "未开盘"
	case hm < 1130:
		st.MarketPhase, st.PhaseLabel = "open", "交易中"
	case hm < 1300:
		st.MarketPhase, st.PhaseLabel = "lunch_break", "午间休市"
	case hm < 1500:
		st.MarketPhase, st.PhaseLabel = "open", "交易中"
	default:
		st.MarketPhase, st.PhaseLabel = "closed", "已收盘"
	}
	return st, nil
}

func fmtNullDate(t sql.NullTime) string {
	if !t.Valid {
		return ""
	}
	return t.Time.Format("2006-01-02")
}
