package datahub

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"quant-system/backend/internal/config"
)

func testClient(baseURL string, mutate func(*config.DatahubConfig)) *Client {
	cfg := config.DatahubConfig{
		BaseURL: baseURL,
		Token:   "tok",
		// 短时长，测试快
		ConnectTimeout: "2s",
		ReadTimeout:    "1s",
		Retries:        0,
		RetryDelay:     "1ms",
		CacheTTL:       "60s",
	}
	if mutate != nil {
		mutate(&cfg)
	}
	return New(cfg)
}

func envelope(data string) string {
	return `{"code":0,"message":"ok","data":` + data + `}`
}

func TestFetchRaw_SuccessAndAuthHeader(t *testing.T) {
	var gotAuth string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		if r.URL.Path != "/v1/datasets/trade_calendar" {
			t.Errorf("path = %s", r.URL.Path)
		}
		if r.URL.Query().Get("start") != "2026-01-01" {
			t.Errorf("start param = %s", r.URL.Query().Get("start"))
		}
		_, _ = w.Write([]byte(envelope(`[{"cal_date":"2026-01-02","is_open":true,"exchange":"SSE"}]`)))
	}))
	defer ts.Close()

	c := testClient(ts.URL, nil)
	raw, err := c.FetchRaw(context.Background(), "trade_calendar", map[string]string{"start": "2026-01-01"})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if gotAuth != "Bearer tok" {
		t.Fatalf("auth header = %q", gotAuth)
	}
	if string(raw) == "" {
		t.Fatal("empty raw")
	}
}

func TestFetchRaw_RetriesOn5xx(t *testing.T) {
	var calls int32
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&calls, 1)
		if n < 3 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte(envelope("[]")))
	}))
	defer ts.Close()

	c := testClient(ts.URL, func(cfg *config.DatahubConfig) { cfg.Retries = 2 })
	if _, err := c.FetchRaw(context.Background(), "x", nil); err != nil {
		t.Fatalf("should succeed after retries: %v", err)
	}
	if got := atomic.LoadInt32(&calls); got != 3 {
		t.Fatalf("calls = %d, want 3", got)
	}
}

func TestFetchRaw_4xxFailsFast(t *testing.T) {
	var calls int32
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"code":1,"message":"bad token"}`))
	}))
	defer ts.Close()

	c := testClient(ts.URL, func(cfg *config.DatahubConfig) { cfg.Retries = 3 })
	_, err := c.FetchRaw(context.Background(), "x", nil)
	var he *HTTPError
	if !errors.As(err, &he) || he.StatusCode != http.StatusUnauthorized {
		t.Fatalf("want HTTPError 401, got %v", err)
	}
	if got := atomic.LoadInt32(&calls); got != 1 {
		t.Fatalf("4xx must not retry: calls = %d", got)
	}
}

func TestFetchRaw_Timeout(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(200 * time.Millisecond)
		_, _ = w.Write([]byte(envelope("[]")))
	}))
	defer ts.Close()

	c := testClient(ts.URL, func(cfg *config.DatahubConfig) {
		cfg.ReadTimeout = "30ms"
		cfg.Retries = 0
	})
	_, err := c.FetchRaw(context.Background(), "x", nil)
	var te *TimeoutError
	if !errors.As(err, &te) {
		t.Fatalf("want TimeoutError, got %v", err)
	}
}

func TestFetchRaw_TTLCache(t *testing.T) {
	var calls int32
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		_, _ = w.Write([]byte(envelope(`[{"code":"000001"}]`)))
	}))
	defer ts.Close()

	c := testClient(ts.URL, nil)
	for i := 0; i < 3; i++ {
		if _, err := c.FetchRaw(context.Background(), "stock_basic", map[string]string{"codes": "000001"}); err != nil {
			t.Fatal(err)
		}
	}
	if got := atomic.LoadInt32(&calls); got != 1 {
		t.Fatalf("cache miss: calls = %d, want 1", got)
	}
	c.Reset()
	if _, err := c.FetchRaw(context.Background(), "stock_basic", map[string]string{"codes": "000001"}); err != nil {
		t.Fatal(err)
	}
	if got := atomic.LoadInt32(&calls); got != 2 {
		t.Fatalf("after Reset calls = %d, want 2", got)
	}
}

func TestParseEnvelope_Variants(t *testing.T) {
	cases := []struct {
		name    string
		body    string
		wantErr bool
	}{
		{"data null → empty", envelope("null"), false},
		{"code!=0 → parse error", `{"code":1,"message":"boom","data":[]}`, true},
		{"non-array data → parse error", `{"code":0,"data":{"a":1}}`, true},
		{"non-json → parse error", `not json`, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := parseEnvelope([]byte(tc.body))
			if tc.wantErr && err == nil {
				t.Fatalf("want error, got nil")
			}
			if !tc.wantErr && err != nil {
				t.Fatalf("want nil, got %v", err)
			}
		})
	}
}

func TestFetch_GenericDecode(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(envelope(
			`[{"code":"000001","name":"平安","list_date":"1991-04-03"}]`)))
	}))
	defer ts.Close()

	type stock struct {
		Code     string `json:"code"`
		Name     string `json:"name"`
		ListDate Date   `json:"list_date"`
	}
	c := testClient(ts.URL, nil)
	rows, err := Fetch[stock](c, context.Background(), "stock_basic", nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].Code != "000001" {
		t.Fatalf("rows = %+v", rows)
	}
	if rows[0].ListDate.Time.Year() != 1991 {
		t.Fatalf("date not parsed: %v", rows[0].ListDate.Time)
	}
}

func TestFetchRaw_Unconfigured(t *testing.T) {
	c := New(config.DatahubConfig{}) // 无 base_url/token
	_, err := c.FetchRaw(context.Background(), "x", nil)
	var pe *ParseError
	if !errors.As(err, &pe) {
		t.Fatalf("want ParseError for unconfigured client, got %v", err)
	}
}

func TestDate_UnmarshalJSON(t *testing.T) {
	var d Date
	if err := json.Unmarshal([]byte(`"2026-10-09"`), &d); err != nil {
		t.Fatal(err)
	}
	if d.Year() != 2026 || d.Month() != 10 || d.Day() != 9 {
		t.Fatalf("got %v", d.Time)
	}
	if err := json.Unmarshal([]byte(`null`), &d); err != nil {
		t.Fatal(err)
	}
	if !d.Time.IsZero() {
		t.Fatalf("null should be zero, got %v", d.Time)
	}
	if err := json.Unmarshal([]byte(`"not-a-date"`), &d); err == nil {
		t.Fatal("want error for bad date")
	}
}
