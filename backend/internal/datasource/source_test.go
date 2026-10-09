package datasource

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"quant-system/backend/internal/config"
)

func newTestSource(t *testing.T, mutate func(*config.DatahubConfig)) (*Source, *[]string) {
	t.Helper()
	var paths []string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path+"?"+r.URL.RawQuery)
		_, _ = w.Write([]byte(`{"code":0,"message":"ok","data":[` +
			`{"code":"000001","name":"平安银行","market":"SZ","industry":"银行",` +
			`"list_date":"1991-04-03","status":"L","universe":"hs300","data_scope":"a_share"}]}`))
	}))
	t.Cleanup(ts.Close)

	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok", ReadDatasets: []string{DsStockBasic},
		ConnectTimeout: "2s", ReadTimeout: "1s", RetryDelay: "1ms", CacheTTL: "0s",
	}
	if mutate != nil {
		mutate(&cfg)
	}
	return New(cfg), &paths
}

func TestEnabledReflectsGate(t *testing.T) {
	s, _ := newTestSource(t, nil)
	if !s.Enabled(DsStockBasic) {
		t.Fatal("stock_basic should be enabled")
	}
	if s.Enabled(DsDailyPrice) {
		t.Fatal("daily_price not in whitelist")
	}

	off, _ := newTestSource(t, func(c *config.DatahubConfig) { c.ReadDatasets = nil })
	if off.Enabled(DsStockBasic) {
		t.Fatal("empty whitelist must disable")
	}
}

func TestStockBasicByCodes(t *testing.T) {
	s, paths := newTestSource(t, nil)
	rows, err := s.StockBasicByCodes(context.Background(), []string{"000001", "600000"})
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("rows = %d", len(rows))
	}
	got := rows[0]
	if got.Code != "000001" || got.Name != "平安银行" || got.ListDate.Year() != 1991 ||
		got.Universe != "hs300" || got.DataScope != "a_share" {
		t.Fatalf("bad conversion: %+v", got)
	}
	if len(*paths) != 1 {
		t.Fatalf("expected 1 HTTP call, got %d", len(*paths))
	}
}

func TestStockBasicByCodes_Empty(t *testing.T) {
	s, paths := newTestSource(t, nil)
	rows, err := s.StockBasicByCodes(context.Background(), nil)
	if err != nil || rows != nil {
		t.Fatalf("empty codes should short-circuit: rows=%v err=%v", rows, err)
	}
	if len(*paths) != 0 {
		t.Fatal("empty codes must not call HTTP")
	}
}

func TestFallbackLocalFlag(t *testing.T) {
	s, _ := newTestSource(t, func(c *config.DatahubConfig) { c.FallbackLocal = true })
	if !s.FallbackLocal() {
		t.Fatal("fallback flag not propagated")
	}
}
