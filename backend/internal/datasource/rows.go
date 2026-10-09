package datasource

import (
	"quant-system/backend/internal/datahub"
	"quant-system/backend/internal/model"
)

// wire DTO：与 datahub 数据集列名逐字对齐（snake_case），日期用 datahub.Date。
// **不直接解进 model.***：model 的 time.Time 解不动 "2026-10-09"，且含 datahub 不供的列。

type stockBasicRow struct {
	Code      string       `json:"code"`
	Name      string       `json:"name"`
	Market    string       `json:"market"`
	Industry  string       `json:"industry"`
	ListDate  datahub.Date `json:"list_date"`
	Status    string       `json:"status"`
	Universe  string       `json:"universe"`
	DataScope string       `json:"data_scope"`
}

type calendarRow struct {
	CalDate  datahub.Date `json:"cal_date"`
	IsOpen   bool         `json:"is_open"`
	Exchange string       `json:"exchange"`
}

type dailyPriceRow struct {
	Code         string       `json:"code"`
	TradeDate    datahub.Date `json:"trade_date"`
	Open         float64      `json:"open"`
	High         float64      `json:"high"`
	Low          float64      `json:"low"`
	Close        float64      `json:"close"`
	Volume       int64        `json:"volume"`
	Amount       float64      `json:"amount"`
	AdjFactor    *float64     `json:"adj_factor"`
	TurnoverRate *float64     `json:"turnover_rate"`
}

type valuationRow struct {
	Code      string       `json:"code"`
	TradeDate datahub.Date `json:"trade_date"`
	Close     float64      `json:"close"`
	TotalMv   float64      `json:"total_mv"`
	FloatMv   float64      `json:"float_mv"`
	PeTtm     *float64     `json:"pe_ttm"`
	PeStatic  *float64     `json:"pe_static"`
	Pb        *float64     `json:"pb"`
}

type financialRow struct {
	Code          string       `json:"code"`
	ReportDate    datahub.Date `json:"report_date"`
	AnnounceDate  datahub.Date `json:"announce_date"`
	PE            *float64     `json:"pe"`
	PB            *float64     `json:"pb"`
	ROE           *float64     `json:"roe"`
	ProfitGrowth  *float64     `json:"profit_growth"`
	RevenueGrowth *float64     `json:"revenue_growth"`
	DebtRatio     *float64     `json:"debt_ratio"`
	GrossMargin   *float64     `json:"gross_margin"`
}

func f64(v *float64) float64 {
	if v == nil {
		return 0
	}
	return *v
}

func toStockBasic(r stockBasicRow) model.StockBasic {
	return model.StockBasic{
		Code: r.Code, Name: r.Name, Market: r.Market, Industry: r.Industry,
		ListDate: r.ListDate.Time, Status: r.Status, Universe: r.Universe,
		DataScope: r.DataScope,
	}
}

func toDailyPrice(r dailyPriceRow) model.DailyPrice {
	return model.DailyPrice{
		Code: r.Code, TradeDate: r.TradeDate.Time,
		Open: r.Open, High: r.High, Low: r.Low, Close: r.Close,
		Volume: r.Volume, Amount: r.Amount,
		AdjFactor: f64(r.AdjFactor), TurnoverRate: r.TurnoverRate,
	}
}

func toValuation(r valuationRow) model.DailyValuation {
	return model.DailyValuation{
		Code: r.Code, TradeDate: r.TradeDate.Time, Close: r.Close,
		TotalMv: r.TotalMv, FloatMv: r.FloatMv,
		PeTtm: f64(r.PeTtm), PeStatic: f64(r.PeStatic), Pb: f64(r.Pb),
	}
}

func toFinancial(r financialRow) model.FinancialIndicator {
	return model.FinancialIndicator{
		Code: r.Code, ReportDate: r.ReportDate.Time, AnnounceDate: r.AnnounceDate.Time,
		PE: f64(r.PE), PB: f64(r.PB), ROE: f64(r.ROE),
		ProfitGrowth: f64(r.ProfitGrowth), RevenueGrowth: f64(r.RevenueGrowth),
		DebtRatio: f64(r.DebtRatio), GrossMargin: f64(r.GrossMargin),
	}
}
