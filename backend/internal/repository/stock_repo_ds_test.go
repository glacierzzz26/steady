package repository

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"quant-system/backend/internal/config"
	"quant-system/backend/internal/datasource"
)

// newTestRepoDS 构造「闸门开」的 StockRepository：db=nil（走 ds 时绝不触库），
// ds 指向 httptest 假 datahub。返回假服务收到的请求串（断言参数透传用）。
// handler 仅实现 codes 过滤——其余过滤/排序由测试断言「参数已下发」，不在假服务复刻。
func newTestRepoDS(t *testing.T) (*StockRepository, *[]string) {
	t.Helper()
	all := []map[string]any{
		{"code": "000001", "name": "平安银行", "market": "SZ", "industry": "银行",
			"list_date": "1991-04-03", "status": "L", "universe": "hs300", "data_scope": "a_share"},
		{"code": "600000", "name": "浦发银行", "market": "SH", "industry": "银行",
			"list_date": "1999-11-10", "status": "L", "universe": "hs300", "data_scope": "a_share"},
		{"code": "600519", "name": "贵州茅台", "market": "SH", "industry": "白酒",
			"list_date": "2001-08-27", "status": "L", "universe": "hs300", "data_scope": "a_share"},
	}
	var paths []string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path+"?"+r.URL.RawQuery)
		rows := all
		if codes := r.URL.Query().Get("codes"); codes != "" {
			want := map[string]bool{}
			for _, c := range strings.Split(codes, ",") {
				want[c] = true
			}
			rows = nil
			for _, m := range all {
				if want[m["code"].(string)] {
					rows = append(rows, m)
				}
			}
		}
		b, _ := json.Marshal(rows)
		_, _ = w.Write([]byte(`{"code":0,"message":"ok","data":` + string(b) + `}`))
	}))
	t.Cleanup(ts.Close)

	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok", ReadDatasets: []string{datasource.DsStockBasic},
		ConnectTimeout: "2s", ReadTimeout: "1s", RetryDelay: "1ms", CacheTTL: "0s",
	}
	return NewStockRepository(nil, datasource.New(cfg)), &paths
}

func TestStockRepoGetListViaDatahub(t *testing.T) {
	r, paths := newTestRepoDS(t)

	stocks, total, err := r.GetList(StockListQuery{
		Page: 1, PageSize: 2, Sort: "list_date", Order: "desc", Keyword: "银行",
	})
	if err != nil {
		t.Fatal(err)
	}
	if total != 3 {
		t.Fatalf("total = %d want 3", total)
	}
	if len(stocks) != 2 || stocks[0].Code != "000001" || stocks[1].Code != "600000" {
		t.Fatalf("page 1 slice wrong: %+v", stocks)
	}
	q := (*paths)[0]
	for _, want := range []string{"sort=list_date", "order=desc", "keyword="} {
		if !strings.Contains(q, want) {
			t.Fatalf("query %q missing %q", q, want)
		}
	}
	// 分页在 Go 侧做：accessor 绝不能下发 limit/offset（否则 total 失真）
	if strings.Contains(q, "limit=") || strings.Contains(q, "offset=") {
		t.Fatalf("must not send limit/offset: %q", q)
	}

	// 第二页：只剩 1 条（切片 [2:3]）
	stocks2, total2, err := r.GetList(StockListQuery{Page: 2, PageSize: 2})
	if err != nil {
		t.Fatal(err)
	}
	if total2 != 3 || len(stocks2) != 1 || stocks2[0].Code != "600519" {
		t.Fatalf("page 2 wrong: total=%d rows=%+v", total2, stocks2)
	}

	// 越界页：空切片、非 nil、total 仍为全量
	stocks3, total3, err := r.GetList(StockListQuery{Page: 99, PageSize: 2})
	if err != nil {
		t.Fatal(err)
	}
	if total3 != 3 || stocks3 == nil || len(stocks3) != 0 {
		t.Fatalf("out-of-range page wrong: total=%d rows=%+v", total3, stocks3)
	}
}

func TestStockRepoGetByCodeViaDatahub(t *testing.T) {
	r, _ := newTestRepoDS(t)

	got, err := r.GetByCode("600519")
	if err != nil {
		t.Fatal(err)
	}
	if got == nil || got.Name != "贵州茅台" || got.Industry != "白酒" || got.DataScope != "a_share" {
		t.Fatalf("bad row: %+v", got)
	}
	if got.ListDate.Year() != 2001 {
		t.Fatalf("list_date not decoded: %v", got.ListDate)
	}

	missing, err := r.GetByCode("999999")
	if err != nil || missing != nil {
		t.Fatalf("missing code should be (nil,nil): got=%+v err=%v", missing, err)
	}
}

func TestStockRepoExistsViaDatahub(t *testing.T) {
	r, _ := newTestRepoDS(t)

	ok, err := r.Exists("000001")
	if err != nil || !ok {
		t.Fatalf("Exists(000001) = %v,%v", ok, err)
	}
	ok, err = r.Exists("999999")
	if err != nil || ok {
		t.Fatalf("Exists(999999) = %v,%v", ok, err)
	}
}

func TestStockRepoGetNamesViaDatahub(t *testing.T) {
	r, _ := newTestRepoDS(t)

	names, err := r.GetNames([]string{"000001", "600519", "999999"})
	if err != nil {
		t.Fatal(err)
	}
	if names["000001"] != "平安银行" || names["600519"] != "贵州茅台" {
		t.Fatalf("names wrong: %+v", names)
	}
	if _, ok := names["999999"]; ok {
		t.Fatalf("missing code must be absent: %+v", names)
	}

	// 空入参短路：不发 HTTP
	empty, _ := newTestRepoDS(t)
	if m, err := empty.GetNames(nil); err != nil || len(m) != 0 {
		t.Fatalf("empty codes: %+v,%v", m, err)
	}
}

// 闸门开但 datahub 故障 ⇒ 失败即抛（默认不回退、不静默给空/陈旧值）。
func TestStockRepoDatahubFailureFailsClosed(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	t.Cleanup(ts.Close)
	cfg := config.DatahubConfig{
		BaseURL: ts.URL, Token: "tok", ReadDatasets: []string{datasource.DsStockBasic},
		ConnectTimeout: "1s", ReadTimeout: "1s", Retries: 0, RetryDelay: "1ms", CacheTTL: "0s",
	}
	r := NewStockRepository(nil, datasource.New(cfg))
	if _, _, err := r.GetList(StockListQuery{Page: 1, PageSize: 20}); err == nil {
		t.Fatal("500 应失败即抛，而非静默返回")
	}
	if _, err := r.GetByCode("600519"); err == nil {
		t.Fatal("500 应失败即抛")
	}
}
