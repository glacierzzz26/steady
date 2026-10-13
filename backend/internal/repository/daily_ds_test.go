package repository

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"
	"time"

	"quant-system/backend/internal/config"
	"quant-system/backend/internal/datasource"
)

func mustDate(t *testing.T, s string) time.Time {
	t.Helper()
	d, err := time.Parse("2006-01-02", s)
	if err != nil {
		t.Fatal(err)
	}
	return d
}

// newPriceDS 构造闸门开的 datahub 读取源，指向 httptest 假服务，按数据集路径分派：
//   - /daily_price       → codes 过滤 + date 精确 / start-end 区间 + asof + adj_not_null +
//     last 窗（每码按 trade_date 降序取前 N）+ 升序 (code, trade_date) 输出
//   - /latest_trade_date → 固定 latestDate（空 → []）
func newPriceDS(t *testing.T, priceRows []map[string]any, latestDate string) (*datasource.Source, *[]string) {
	t.Helper()
	var paths []string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path+"?"+r.URL.RawQuery)
		q := r.URL.Query()
		ok := func(rows []map[string]any) {
			b, _ := json.Marshal(rows)
			_, _ = w.Write([]byte(`{"code":0,"message":"ok","data":` + string(b) + `}`))
		}
		switch {
		case strings.HasSuffix(r.URL.Path, "/latest_trade_date"):
			if latestDate == "" {
				ok([]map[string]any{})
				return
			}
			ok([]map[string]any{{"trade_date": latestDate}})
		case strings.HasSuffix(r.URL.Path, "/daily_price"):
			want := map[string]bool{}
			for _, c := range strings.Split(q.Get("codes"), ",") {
				if c != "" {
					want[c] = true
				}
			}
			sel := []map[string]any{}
			for _, m := range priceRows {
				if len(want) == 0 || want[m["code"].(string)] {
					sel = append(sel, m)
				}
			}
			td := func(m map[string]any) string { return m["trade_date"].(string) }
			keep := func(pred func(m map[string]any) bool) {
				f := sel[:0]
				for _, m := range sel {
					if pred(m) {
						f = append(f, m)
					}
				}
				sel = f
			}
			if d := q.Get("date"); d != "" {
				keep(func(m map[string]any) bool { return td(m) == d })
			} else {
				if s := q.Get("start"); s != "" {
					keep(func(m map[string]any) bool { return td(m) >= s })
				}
				if e := q.Get("end"); e != "" {
					keep(func(m map[string]any) bool { return td(m) <= e })
				}
			}
			if a := q.Get("asof"); a != "" {
				keep(func(m map[string]any) bool { return td(m) <= a })
			}
			if q.Get("adj_not_null") == "true" {
				keep(func(m map[string]any) bool { return m["adj_factor"] != nil })
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
			ok(sel)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(ts.Close)
	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok",
		ReadDatasets:   []string{datasource.DsDailyPrice, datasource.DsLatestTradeDate},
		ConnectTimeout: "2s", ReadTimeout: "1s", RetryDelay: "1ms", CacheTTL: "0s",
	}
	return datasource.New(cfg), &paths
}

func bar(code, td string, close float64, adj any) map[string]any {
	return map[string]any{"code": code, "trade_date": td, "open": close, "high": close,
		"low": close, "close": close, "volume": 100, "amount": 1000.0,
		"adj_factor": adj, "turnover_rate": nil}
}

func TestDailyRepoGetRangeViaDatahub(t *testing.T) {
	ds, paths := newPriceDS(t, []map[string]any{
		bar("000001", "2026-08-01", 10, 1.0),
		bar("000001", "2026-08-02", 11, 1.0),
	}, "")
	r := NewDailyRepository(nil, ds)

	bars, err := r.GetRange("000001", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(bars) != 2 || bars[0].Close != 10 || bars[1].Close != 11 {
		t.Fatalf("bars wrong: %+v", bars)
	}
	q := (*paths)[0]
	if !strings.Contains(q, "codes=000001") || strings.Contains(q, "start=") || strings.Contains(q, "last=") {
		t.Fatalf("range query wrong: %q", q)
	}
}

func TestDailyRepoGetLatestViaDatahub(t *testing.T) {
	ds, paths := newPriceDS(t, []map[string]any{
		bar("000001", "2026-08-01", 10, 1.0),
		bar("000001", "2026-08-02", 11, 1.0),
	}, "")
	r := NewDailyRepository(nil, ds)

	got, err := r.GetLatest("000001")
	if err != nil || got == nil || got.Close != 11 {
		t.Fatalf("latest wrong: %+v err=%v", got, err)
	}
	if q := (*paths)[0]; !strings.Contains(q, "last=1") {
		t.Fatalf("query %q missing last=1", q)
	}
	miss, err := r.GetLatest("999999")
	if err != nil || miss != nil {
		t.Fatalf("missing code should be (nil,nil): %+v,%v", miss, err)
	}
}

func TestDailyRepoGetLatestFactorViaDatahub(t *testing.T) {
	ds, paths := newPriceDS(t, []map[string]any{
		bar("000001", "2026-08-01", 10, 1.5),
		bar("000001", "2026-08-02", 11, nil), // 最新一期空 → 应被 adj_not_null 滤除
	}, "")
	r := NewDailyRepository(nil, ds)

	v, ok, err := r.GetLatestFactor("000001")
	if err != nil || !ok || v != 1.5 {
		t.Fatalf("factor wrong: v=%v ok=%v err=%v", v, ok, err)
	}
	q := (*paths)[0]
	if !strings.Contains(q, "adj_not_null=true") || !strings.Contains(q, "last=1") {
		t.Fatalf("factor query wrong: %q", q)
	}
}

func TestDailyRepoGetLatestTradeDateViaDatahub(t *testing.T) {
	ds, paths := newPriceDS(t, nil, "2026-08-05")
	r := NewDailyRepository(nil, ds)

	td, err := r.GetLatestTradeDate()
	if err != nil || td == nil || td.Format("2006-01-02") != "2026-08-05" {
		t.Fatalf("latest trade date wrong: %+v err=%v", td, err)
	}
	if q := (*paths)[0]; !strings.Contains(q, "exclude_index=false") {
		t.Fatalf("query %q missing exclude_index=false", q)
	}
}

func TestDailyRepoGetIndexQuotesViaDatahub(t *testing.T) {
	ds, paths := newPriceDS(t, []map[string]any{
		bar("sh000300", "2026-08-01", 4000, nil),
		bar("sh000300", "2026-08-02", 4050, nil),
		bar("sh000300", "2026-08-03", 4100, nil),
		bar("sh000001", "2026-08-02", 3200, nil),
		bar("sh000001", "2026-08-03", 3250, nil),
	}, "")
	r := NewDailyRepository(nil, ds)

	bars, err := r.GetIndexQuotes([]string{"sh000300", "sh000001"})
	if err != nil {
		t.Fatal(err)
	}
	// 每码最近 2 行，升序 → 4 行
	if len(bars) != 4 {
		t.Fatalf("want 4 bars, got %d: %+v", len(bars), bars)
	}
	if bars[0].Code != "sh000001" || bars[0].TradeDate.Format("2006-01-02") != "2026-08-02" {
		t.Fatalf("bars[0] wrong: %+v", bars[0])
	}
	if q := (*paths)[0]; !strings.Contains(q, "last=2") {
		t.Fatalf("query %q missing last=2", q)
	}
}

func TestStockRepoGetPoolMarketViaDatahub(t *testing.T) {
	ds, paths := newPriceDS(t, []map[string]any{
		bar("000001", "2026-08-01", 100, nil),
		bar("000001", "2026-08-02", 110, nil),
		bar("600000", "2026-08-02", 50, nil), // 仅一行 → chg 空
	}, "")
	r := NewStockRepository(nil, ds)

	got, err := r.GetPoolMarket([]string{"000001", "600000"})
	if err != nil {
		t.Fatal(err)
	}
	if got["000001"] == nil || got["000001"].Price == nil || *got["000001"].Price != 110 ||
		got["000001"].Chg == nil || math.Abs(*got["000001"].Chg-10.0) > 1e-9 {
		t.Fatalf("000001 wrong: %+v", got["000001"])
	}
	if got["600000"] == nil || got["600000"].Chg != nil {
		t.Fatalf("600000 chg should be nil: %+v", got["600000"])
	}
	if len(*paths) != 1 {
		t.Fatalf("应一次往返（无 N+1），got %d calls", len(*paths))
	}
}

func TestSignalRepoGetSignalChg20ViaDatahub(t *testing.T) {
	var rows []map[string]any
	for i := 1; i <= 21; i++ { // close 1..21 → qfq 同（adj 空）
		rows = append(rows, bar("600000", fmt.Sprintf("2026-06-%02d", i), float64(i), nil))
	}
	for i := 1; i <= 20; i++ { // 仅 20 行 → 不足 21，应无行
		rows = append(rows, bar("600001", fmt.Sprintf("2026-06-%02d", i), float64(i), nil))
	}
	ds, paths := newPriceDS(t, rows, "")
	r := NewSignalRepository(nil, ds)

	date := mustDate(t, "2026-06-21")
	got, err := r.GetSignalChg20([]string{"600000", "600001"}, date)
	if err != nil {
		t.Fatal(err)
	}
	// (21/1 - 1)*100 = 2000
	if got["600000"] != 2000.0 {
		t.Fatalf("600000 chg20 wrong: %v", got["600000"])
	}
	if _, ok := got["600001"]; ok {
		t.Fatalf("600001 (<21 bars) 应无行: %+v", got)
	}
	q := (*paths)[0]
	if !strings.Contains(q, "last=21") || !strings.Contains(q, "asof=2026-06-21") {
		t.Fatalf("chg20 query wrong: %q", q)
	}
}

func TestDailyRepoPrefetchDayViaDatahub(t *testing.T) {
	ds, paths := newPriceDS(t, []map[string]any{
		// 000001：当日 bar（d）+ 前一交易日（dPrev）→ GetByDate=d、GetPrevClose=dPrev
		bar("000001", "2026-08-01", 10, 1.0),
		bar("000001", "2026-08-02", 11, 1.0),
		// 600000：当日停牌（两行均 < d）→ GetByDate=nil、GetPrevClose=最近一行（08-01）
		bar("600000", "2026-07-30", 5, 1.0),
		bar("600000", "2026-08-01", 6, 1.0),
		// 300001：当日上市（仅当日一行）→ GetByDate=当日、GetPrevClose=(0,false)
		bar("300001", "2026-08-02", 20, 1.0),
	}, "")
	r := NewDailyRepository(nil, ds)
	d := mustDate(t, "2026-08-02")

	if err := r.PrefetchDay(context.Background(), []string{"000001", "600000", "300001"}, d); err != nil {
		t.Fatalf("PrefetchDay: %v", err)
	}
	if len(*paths) != 1 {
		t.Fatalf("应恰一次往返，got %d calls: %v", len(*paths), *paths)
	}
	q := (*paths)[0]
	if !strings.Contains(q, "last=2") || !strings.Contains(q, "asof=2026-08-02") {
		t.Fatalf("预取查询应含 last=2 & asof: %q", q)
	}

	// 000001：当日 + 前收
	if bar, err := r.GetByDate("000001", d); err != nil || bar == nil || bar.Close != 11 {
		t.Fatalf("000001 GetByDate wrong: %+v err=%v", bar, err)
	}
	if c, ok, err := r.GetPrevClose("000001", d); err != nil || !ok || c != 10 {
		t.Fatalf("000001 GetPrevClose wrong: c=%v ok=%v err=%v", c, ok, err)
	}

	// 600000：停牌 → 当日无 bar，前收取最近一行
	if bar, err := r.GetByDate("600000", d); err != nil || bar != nil {
		t.Fatalf("600000 停牌 GetByDate 应 (nil,nil): %+v err=%v", bar, err)
	}
	if c, ok, err := r.GetPrevClose("600000", d); err != nil || !ok || c != 6 {
		t.Fatalf("600000 停牌 GetPrevClose wrong: c=%v ok=%v err=%v", c, ok, err)
	}

	// 300001：当日上市 → 无前收
	if bar, err := r.GetByDate("300001", d); err != nil || bar == nil || bar.Close != 20 {
		t.Fatalf("300001 GetByDate wrong: %+v err=%v", bar, err)
	}
	if _, ok, err := r.GetPrevClose("300001", d); err != nil || ok {
		t.Fatalf("300001 无前收应 (0,false): ok=%v err=%v", ok, err)
	}

	// 快照外 code：无行 → GetByDate=nil、GetPrevClose=(0,false)
	if bar, err := r.GetByDate("999999", d); err != nil || bar != nil {
		t.Fatalf("快照缺码 GetByDate 应 (nil,nil): %+v err=%v", bar, err)
	}
	if _, ok, _ := r.GetPrevClose("999999", d); ok {
		t.Fatal("快照缺码 GetPrevClose 应 (0,false)")
	}
}

func TestDailyRepoPrefetchDayFailClosed(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	t.Cleanup(ts.Close)
	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok",
		ReadDatasets:   []string{datasource.DsDailyPrice},
		ConnectTimeout: "1s", ReadTimeout: "1s", Retries: 0, RetryDelay: "1ms", CacheTTL: "0s",
	}
	r := NewDailyRepository(nil, datasource.New(cfg))
	err := r.PrefetchDay(context.Background(), []string{"000001"}, mustDate(t, "2026-08-02"))
	if err == nil {
		t.Fatal("500 应失败即抛（fail-closed）")
	}
}

func TestDailyRepoPrefetchDayNoOpWhenGateOff(t *testing.T) {
	// ds 未注入（闸门关形态）→ no-op，不 panic
	if err := NewDailyRepository(nil).PrefetchDay(context.Background(),
		[]string{"000001"}, mustDate(t, "2026-08-02")); err != nil {
		t.Fatalf("闸门关 PrefetchDay 应 no-op: %v", err)
	}
	// codes 为空 → no-op（闸门开亦不发请求）
	ds, paths := newPriceDS(t, nil, "")
	if err := NewDailyRepository(nil, ds).PrefetchDay(context.Background(),
		nil, mustDate(t, "2026-08-02")); err != nil {
		t.Fatalf("空 codes PrefetchDay 应 no-op: %v", err)
	}
	if len(*paths) != 0 {
		t.Fatalf("空 codes 不应发请求: %v", *paths)
	}
}

func TestDailyDatahubFailureFailsClosed(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	t.Cleanup(ts.Close)
	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok",
		ReadDatasets:   []string{datasource.DsDailyPrice, datasource.DsLatestTradeDate},
		ConnectTimeout: "1s", ReadTimeout: "1s", Retries: 0, RetryDelay: "1ms", CacheTTL: "0s",
	}
	ds := datasource.New(cfg)
	if _, err := NewDailyRepository(nil, ds).GetRange("000001", nil, nil); err == nil {
		t.Fatal("500 应失败即抛")
	}
	if _, err := NewDailyRepository(nil, ds).GetLatest("000001"); err == nil {
		t.Fatal("500 应失败即抛")
	}
	if _, _, err := NewDailyRepository(nil, ds).GetLatestFactor("000001"); err == nil {
		t.Fatal("500 应失败即抛")
	}
	if _, err := NewDailyRepository(nil, ds).GetIndexQuotes([]string{"sh000300"}); err == nil {
		t.Fatal("500 应失败即抛")
	}
	if _, err := NewStockRepository(nil, ds).GetPoolMarket([]string{"000001"}); err == nil {
		t.Fatal("500 应失败即抛")
	}
	if _, err := NewSignalRepository(nil, ds).GetSignalChg20([]string{"600000"}, mustDate(t, "2026-06-21")); err == nil {
		t.Fatal("500 应失败即抛")
	}
}
