package service

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"

	"quant-system/backend/internal/config"
	"quant-system/backend/internal/datasource"
	"quant-system/backend/internal/model"
)

// 闸门开（daily_price 走 datahub）下的 ExecuteDay 集成测试（TEST_DB_DSN 门控）。
// 场景：本地 daily_price 已停采冻结（当日无 bar），假 datahub 有当日 bar ——
// 预取快照（DailyRepository.PrefetchDay）让 mark-to-market / 策略撮合仍读到当日行情，
// 证明「Tier3 停摆」已修复；并断言 daily_price 恰一次往返（批量预取，非逐码 N+1）。

func fakeBar(code, td string, close float64) map[string]any {
	return map[string]any{"code": code, "trade_date": td, "open": close, "high": close,
		"low": close, "close": close, "volume": 100, "amount": 1000.0,
		"adj_factor": 1.0, "turnover_rate": nil}
}

// newFakeDailyDS 假 datahub 源：仅注册 daily_price，支持 codes 过滤 + asof + last 窗。
// 返回源 + 调用记录（*[]string）；断言 len == 1 即「恰一次往返」。
func newFakeDailyDS(t *testing.T, rows []map[string]any) (*datasource.Source, *[]string) {
	t.Helper()
	var paths []string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path+"?"+r.URL.RawQuery)
		q := r.URL.Query()
		want := map[string]bool{}
		for _, c := range strings.Split(q.Get("codes"), ",") {
			if c != "" {
				want[c] = true
			}
		}
		sel := []map[string]any{}
		for _, m := range rows {
			if len(want) == 0 || want[m["code"].(string)] {
				sel = append(sel, m)
			}
		}
		td := func(m map[string]any) string { return m["trade_date"].(string) }
		if a := q.Get("asof"); a != "" {
			f := sel[:0]
			for _, m := range sel {
				if td(m) <= a {
					f = append(f, m)
				}
			}
			sel = f
		}
		if last := q.Get("last"); last != "" {
			n := 0
			fmt.Sscanf(last, "%d", &n)
			best := map[string][]map[string]any{}
			for _, m := range sel {
				best[m["code"].(string)] = append(best[m["code"].(string)], m)
			}
			sel = sel[:0]
			for _, ms := range best {
				sort.Slice(ms, func(i, j int) bool { return td(ms[i]) > td(ms[j]) })
				if len(ms) > n {
					ms = ms[:n]
				}
				sel = append(sel, ms...)
			}
		}
		sort.Slice(sel, func(i, j int) bool {
			if sel[i]["code"] != sel[j]["code"] {
				return sel[i]["code"].(string) < sel[j]["code"].(string)
			}
			return td(sel[i]) < td(sel[j])
		})
		b, _ := json.Marshal(sel)
		_, _ = w.Write([]byte(`{"code":0,"message":"ok","data":` + string(b) + `}`))
	}))
	t.Cleanup(ts.Close)
	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok",
		ReadDatasets:   []string{datasource.DsDailyPrice},
		ConnectTimeout: "2s", ReadTimeout: "1s", RetryDelay: "1ms", CacheTTL: "0s",
	}
	return datasource.New(cfg), &paths
}

func TestExecuteDayPrefetchSnapshotFixStall(t *testing.T) {
	db := freshDB(t)
	d := consistencyDay(t, "2026-09-20")
	accID := seedRiskBase(t, db, `{"top_n":10,"max_position_pct":0.20}`)
	if err := db.Model(&model.Account{}).Where("id = ?", accID).
		Updates(map[string]interface{}{"cash": 100000.00}).Error; err != nil {
		t.Fatal(err)
	}
	// 持仓 600000（本地无当日 bar，快照须从 datahub 取当日 close 6.0）
	if err := db.Create(&model.Position{
		AccountID: accID, Code: "600000", Quantity: 500, AvailableQty: 500,
		CostPrice: 5.00, CurrentPrice: 5.00, MarketValue: 2500,
	}).Error; err != nil {
		t.Fatalf("seed position: %v", err)
	}
	// 当日 BUY 信号 600001（无快照则被当停牌跳过）
	if err := db.Create(&model.StrategySignal{
		StrategyName: "multi_factor", Code: "600001", TradeDate: d,
		Score: 90, Action: model.ActionBuy, Reason: "测试",
	}).Error; err != nil {
		t.Fatalf("seed signal: %v", err)
	}

	// 本地 daily_price 完全不种子（模拟停采冻结）；datahub 假源提供当日 bar。
	// 价格刻意在 ±10% 内（避免涨停拒单）：600000 5.0→6.0 会超限 → 用 5.0→5.1；
	// 600001 10.0→10.2（+2%，正常成交）。
	ds, pathList := newFakeDailyDS(t, []map[string]any{
		fakeBar("600000", "2026-09-19", 5.0),
		fakeBar("600000", "2026-09-20", 5.1),
		fakeBar("600001", "2026-09-19", 10.0),
		fakeBar("600001", "2026-09-20", 10.2),
	})

	svc := NewTradingService(db, config.AccountConfig{
		InitialCash: 100000, CommissionRate: 0.00025, MinCommission: 5.0,
		StampTaxRate: 0.0005, Slippage: 0.001,
	}, ds)
	res, err := svc.ExecuteDay(accID, d)
	if err != nil {
		t.Fatalf("ExecuteDay: %v", err)
	}
	if res.Skipped {
		t.Fatal("fixture 不应触发幂等跳过")
	}
	// 快照生效 → BUY 600001 成交（无快照则本地无当日行情 → BuyCount=0）
	if res.BuyCount != 1 {
		t.Fatalf("快照应使 BUY 600001 成交：BuyCount=%d want 1", res.BuyCount)
	}
	if len(*pathList) != 1 {
		t.Fatalf("daily_price 应恰一次往返（批量预取）：got %d: %v", len(*pathList), *pathList)
	}
	if q := (*pathList)[0]; !strings.Contains(q, "last=2") || !strings.Contains(q, "asof=2026-09-20") {
		t.Fatalf("预取查询应含 last=2 & asof=2026-09-20：%q", q)
	}
	// mark-to-market：600000 现价 = 快照当日 close 5.1
	var pos model.Position
	if err := db.Where("account_id = ? AND code = ?", accID, "600000").First(&pos).Error; err != nil {
		t.Fatalf("持仓应保留: %v", err)
	}
	if pos.CurrentPrice != 5.1 {
		t.Errorf("mark-to-market 现价 = %v, want 5.1（快照当日 close）", pos.CurrentPrice)
	}
}
