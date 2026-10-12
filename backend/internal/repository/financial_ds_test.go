package repository

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"

	"quant-system/backend/internal/config"
	"quant-system/backend/internal/datasource"
)

// newFinancialDS 构造闸门开的 datahub 读取源，指向 httptest 假服务。
// 假服务实现 codes 过滤 + last 窗（每码按 announce_date/report_date 降序取前 N），
// 使 last=1 归约结果正确（本侧断言归约 + 参数透传）。
func newFinancialDS(t *testing.T, rows []map[string]any) (*datasource.Source, *[]string) {
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
		if last := q.Get("last"); last == "1" {
			// 每码：announce_date/report_date 降序取第一条
			best := map[string]map[string]any{}
			for _, m := range sel {
				c := m["code"].(string)
				cur, ok := best[c]
				if !ok || m["announce_date"].(string) > cur["announce_date"].(string) {
					best[c] = m
				}
			}
			sel = sel[:0]
			for _, m := range best {
				sel = append(sel, m)
			}
		}
		sort.Slice(sel, func(i, j int) bool {
			if sel[i]["code"] != sel[j]["code"] {
				return sel[i]["code"].(string) < sel[j]["code"].(string)
			}
			return sel[i]["report_date"].(string) < sel[j]["report_date"].(string)
		})
		b, _ := json.Marshal(sel)
		_, _ = w.Write([]byte(`{"code":0,"message":"ok","data":` + string(b) + `}`))
	}))
	t.Cleanup(ts.Close)
	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok", ReadDatasets: []string{datasource.DsFinancialIndicator},
		ConnectTimeout: "2s", ReadTimeout: "1s", RetryDelay: "1ms", CacheTTL: "0s",
	}
	return datasource.New(cfg), &paths
}

func financialRows() []map[string]any {
	return []map[string]any{
		{"code": "000001", "report_date": "2026-03-31", "announce_date": "2026-04-25",
			"pe": 4.9, "pb": 0.79, "roe": 11.0, "profit_growth": 1.0,
			"revenue_growth": 1.0, "debt_ratio": 90.0, "gross_margin": 1.0},
		{"code": "000001", "report_date": "2026-06-30", "announce_date": "2026-07-20",
			"pe": 5.0, "pb": 0.80, "roe": 12.5, "profit_growth": 1.0,
			"revenue_growth": 1.0, "debt_ratio": 89.5, "gross_margin": 1.0},
		{"code": "600519", "report_date": "2026-06-30", "announce_date": "2026-07-15",
			"pe": 30.0, "pb": 8.0, "roe": 30.0, "profit_growth": 1.0,
			"revenue_growth": 1.0, "debt_ratio": 20.0, "gross_margin": 1.0},
		// 最新一期 roe 为空 → pool 读点应滤除
		{"code": "600000", "report_date": "2026-06-30", "announce_date": "2026-07-10",
			"pe": nil, "pb": nil, "roe": nil, "profit_growth": nil,
			"revenue_growth": nil, "debt_ratio": nil, "gross_margin": nil},
	}
}

func TestFinancialRepoGetByCodeViaDatahub(t *testing.T) {
	ds, paths := newFinancialDS(t, financialRows())
	r := NewFinancialRepository(nil, ds)

	got, err := r.GetByCode("000001", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("want 2 rows, got %d: %+v", len(got), got)
	}
	// report_date DESC
	if got[0].ReportDate.Format("2006-01-02") != "2026-06-30" ||
		got[1].ReportDate.Format("2006-01-02") != "2026-03-31" {
		t.Fatalf("order wrong: %+v", got)
	}
	if got[0].ROE != 12.5 {
		t.Fatalf("roe wrong: %+v", got[0])
	}
	// limit 生效
	lim, err := r.GetByCode("000001", 1)
	if err != nil || len(lim) != 1 || lim[0].ROE != 12.5 {
		t.Fatalf("limit wrong: %+v err=%v", lim, err)
	}
	if strings.Contains((*paths)[0], "last=") {
		t.Fatalf("GetByCode 不应下发 last: %q", (*paths)[0])
	}
}

func TestFinancialRepoGetLatestByAnnounceViaDatahub(t *testing.T) {
	ds, paths := newFinancialDS(t, financialRows())
	r := NewFinancialRepository(nil, ds)

	got, err := r.GetLatestByAnnounce("000001")
	if err != nil {
		t.Fatal(err)
	}
	// 最新一期按 announce_date（2026-07-20）
	if got == nil || got.AnnounceDate.Format("2006-01-02") != "2026-07-20" || got.ROE != 12.5 {
		t.Fatalf("latest wrong: %+v", got)
	}
	if q := (*paths)[0]; !strings.Contains(q, "last=1") {
		t.Fatalf("query %q missing last=1", q)
	}
	// 未命中 → (nil, nil)
	miss, err := r.GetLatestByAnnounce("999999")
	if err != nil || miss != nil {
		t.Fatalf("missing code should be (nil,nil): %+v,%v", miss, err)
	}
}

func TestStockRepoGetPoolFinancialViaDatahub(t *testing.T) {
	ds, paths := newFinancialDS(t, financialRows())
	r := NewStockRepository(nil, ds)

	got, err := r.GetPoolFinancial([]string{"000001", "600519", "600000"})
	if err != nil {
		t.Fatal(err)
	}
	if got["000001"] == nil || got["000001"].Roe == nil || *got["000001"].Roe != 12.5 {
		t.Fatalf("000001 wrong: %+v", got["000001"])
	}
	if got["600519"] == nil || *got["600519"].Roe != 30.0 {
		t.Fatalf("600519 wrong: %+v", got["600519"])
	}
	// roe 为空 → 滤除（对齐 SQL roe IS NOT NULL）
	if _, ok := got["600000"]; ok {
		t.Fatalf("null-roe row must be absent: %+v", got)
	}
	if q := (*paths)[0]; !strings.Contains(q, "last=1") {
		t.Fatalf("query %q missing last=1", q)
	}
}

func TestFinancialDatahubFailureFailsClosed(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	t.Cleanup(ts.Close)
	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok", ReadDatasets: []string{datasource.DsFinancialIndicator},
		ConnectTimeout: "1s", ReadTimeout: "1s", Retries: 0, RetryDelay: "1ms", CacheTTL: "0s",
	}
	ds := datasource.New(cfg)
	if _, err := NewFinancialRepository(nil, ds).GetByCode("000001", 10); err == nil {
		t.Fatal("500 应失败即抛")
	}
	if _, err := NewFinancialRepository(nil, ds).GetLatestByAnnounce("000001"); err == nil {
		t.Fatal("500 应失败即抛")
	}
	if _, err := NewStockRepository(nil, ds).GetPoolFinancial([]string{"000001"}); err == nil {
		t.Fatal("500 应失败即抛")
	}
}
