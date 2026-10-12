package repository

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"quant-system/backend/internal/config"
	"quant-system/backend/internal/datasource"
)

// newValuationDS 构造闸门开的 datahub 读取源，指向 httptest 假服务
// （仅按 codes 过滤；last/asof 由真实 datahub 生效，本假服务只回全部匹配行，
// 供断言「参数已下发」+ 归约结果）。返回假服务收到的请求串。
func newValuationDS(t *testing.T, rows []map[string]any) (*datasource.Source, *[]string) {
	t.Helper()
	var paths []string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path+"?"+r.URL.RawQuery)
		want := map[string]bool{}
		for _, c := range strings.Split(r.URL.Query().Get("codes"), ",") {
			if c != "" {
				want[c] = true
			}
		}
		out := []map[string]any{}
		for _, m := range rows {
			if want[m["code"].(string)] {
				out = append(out, m)
			}
		}
		b, _ := json.Marshal(out)
		_, _ = w.Write([]byte(`{"code":0,"message":"ok","data":` + string(b) + `}`))
	}))
	t.Cleanup(ts.Close)
	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok", ReadDatasets: []string{datasource.DsDailyValuation},
		ConnectTimeout: "2s", ReadTimeout: "1s", RetryDelay: "1ms", CacheTTL: "0s",
	}
	return datasource.New(cfg), &paths
}

// valuationRows 升序（code, trade_date）——与 datahub daily_valuation 输出一致，
// 故「末行 = 最新」成立。
func valuationRows() []map[string]any {
	return []map[string]any{
		{"code": "000001", "trade_date": "2026-08-03", "close": 5.0,
			"total_mv": 1e9, "float_mv": 9e8, "pe_ttm": 4.9, "pe_static": 5.0, "pb": 0.79},
		{"code": "000001", "trade_date": "2026-08-04", "close": 5.1,
			"total_mv": 1.02e9, "float_mv": 9.1e8, "pe_ttm": 5.0, "pe_static": 5.1, "pb": 0.80},
		{"code": "600519", "trade_date": "2026-08-04", "close": 30.0,
			"total_mv": 3e9, "float_mv": 3e9, "pe_ttm": nil, "pe_static": nil, "pb": nil},
	}
}

func TestDailyRepoGetLatestValuationViaDatahub(t *testing.T) {
	ds, paths := newValuationDS(t, valuationRows())
	r := NewDailyRepository(nil, ds) // db=nil：闸门开时绝不触库

	got, err := r.GetLatestValuation("000001")
	if err != nil {
		t.Fatal(err)
	}
	if got == nil || got.Code != "000001" {
		t.Fatalf("bad row: %+v", got)
	}
	if !got.TradeDate.Equal(time.Date(2026, 8, 4, 0, 0, 0, 0, time.UTC)) {
		t.Fatalf("want latest 2026-08-04, got %v", got.TradeDate)
	}
	if got.PeTtm != 5.0 || got.Pb != 0.80 {
		t.Fatalf("pe/pb wrong: %+v", got)
	}
	q := (*paths)[0]
	if !strings.Contains(q, "/v1/datasets/daily_valuation") || !strings.Contains(q, "last=1") {
		t.Fatalf("query %q missing dataset/last=1", q)
	}

	// 无该码 → (nil, nil)
	missing, err := r.GetLatestValuation("999999")
	if err != nil || missing != nil {
		t.Fatalf("missing code should be (nil,nil): got=%+v err=%v", missing, err)
	}
}

func TestSignalRepoGetSignalPeViaDatahub(t *testing.T) {
	ds, paths := newValuationDS(t, valuationRows())
	r := NewSignalRepository(nil, ds)
	date := time.Date(2026, 8, 4, 0, 0, 0, 0, time.UTC)

	got, err := r.GetSignalPe([]string{"000001", "600519"}, date)
	if err != nil {
		t.Fatal(err)
	}
	if got["000001"] != 5.0 {
		t.Fatalf("000001 pe = %v want 5.0", got["000001"])
	}
	// 600519 最新一行 pe_ttm 为 NULL → 应被滤除（对齐 SQL pe_ttm IS NOT NULL）
	if _, ok := got["600519"]; ok {
		t.Fatalf("600519 null-pe row must be absent: %+v", got)
	}
	q := (*paths)[0]
	for _, want := range []string{"last=1", "asof=2026-08-04"} {
		if !strings.Contains(q, want) {
			t.Fatalf("query %q missing %q", q, want)
		}
	}

	// 空入参短路：不发 HTTP
	empty, err := r.GetSignalPe(nil, date)
	if err != nil || len(empty) != 0 {
		t.Fatalf("empty codes: %+v,%v", empty, err)
	}
}

func TestStockRepoGetPoolValuationViaDatahub(t *testing.T) {
	rows := []map[string]any{
		{"code": "000001", "trade_date": "2026-08-04", "pe_ttm": 5.0, "pb": 0.80},
		{"code": "600000", "trade_date": "2026-08-04", "pe_ttm": nil, "pb": 0.60},
		{"code": "600519", "trade_date": "2026-08-04", "pe_ttm": nil, "pb": nil},
	}
	ds, paths := newValuationDS(t, rows)
	r := NewStockRepository(nil, ds)

	got, err := r.GetPoolValuation([]string{"000001", "600000", "600519"})
	if err != nil {
		t.Fatal(err)
	}
	if got["000001"] == nil || got["000001"].Pe == nil || *got["000001"].Pe != 5.0 || *got["000001"].Pb != 0.80 {
		t.Fatalf("000001 wrong: %+v", got["000001"])
	}
	// pe 空 pb 有值 → 保留（Pe nil, Pb 有值）
	if got["600000"] == nil || got["600000"].Pe != nil || got["600000"].Pb == nil {
		t.Fatalf("600000 wrong: %+v", got["600000"])
	}
	// pe/pb 全空 → 滤除（对齐 SQL pe_ttm IS NOT NULL OR pb IS NOT NULL）
	if _, ok := got["600519"]; ok {
		t.Fatalf("both-null row must be absent: %+v", got)
	}
	if q := (*paths)[0]; !strings.Contains(q, "last=1") {
		t.Fatalf("query %q missing last=1", q)
	}
}

// 闸门开但 datahub 故障 ⇒ 失败即抛（默认不回退）。
func TestValuationDatahubFailureFailsClosed(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	t.Cleanup(ts.Close)
	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok", ReadDatasets: []string{datasource.DsDailyValuation},
		ConnectTimeout: "1s", ReadTimeout: "1s", Retries: 0, RetryDelay: "1ms", CacheTTL: "0s",
	}
	ds := datasource.New(cfg)
	if _, err := NewDailyRepository(nil, ds).GetLatestValuation("000001"); err == nil {
		t.Fatal("500 应失败即抛")
	}
	if _, err := NewSignalRepository(nil, ds).GetSignalPe(
		[]string{"000001"}, time.Now()); err == nil {
		t.Fatal("500 应失败即抛")
	}
	if _, err := NewStockRepository(nil, ds).GetPoolValuation(
		[]string{"000001"}); err == nil {
		t.Fatal("500 应失败即抛")
	}
}
