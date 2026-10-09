package config

import "testing"

func TestDatahubReadEnabled(t *testing.T) {
	base := DatahubConfig{BaseURL: "http://datahub:8100", Token: "tok", ReadDatasets: []string{"stock_basic"}}
	if !base.ReadEnabled("stock_basic") {
		t.Fatal("should be enabled")
	}
	if base.ReadEnabled("trade_calendar") {
		t.Fatal("dataset not in whitelist must be disabled")
	}

	noToken := base
	noToken.Token = ""
	if noToken.ReadEnabled("stock_basic") {
		t.Fatal("empty token must disable (fail-closed)")
	}

	noBase := base
	noBase.BaseURL = ""
	if noBase.ReadEnabled("stock_basic") {
		t.Fatal("empty base_url must disable")
	}

	empty := DatahubConfig{}
	if empty.ReadEnabled("stock_basic") {
		t.Fatal("zero config must disable")
	}
}

func TestGetEnvListAndBool(t *testing.T) {
	t.Setenv("TEST_LIST", " a , b ,, c ")
	got := getEnvList("TEST_LIST", nil)
	want := []string{"a", "b", "c"}
	if len(got) != len(want) {
		t.Fatalf("got %v want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v want %v", got, want)
		}
	}
	if fb := getEnvList("TEST_LIST_UNSET", []string{"x"}); len(fb) != 1 || fb[0] != "x" {
		t.Fatalf("fallback = %v", fb)
	}

	t.Setenv("TEST_BOOL", "YES")
	if !getEnvBool("TEST_BOOL", false) {
		t.Fatal("YES should be true")
	}
	t.Setenv("TEST_BOOL", "0")
	if getEnvBool("TEST_BOOL", true) {
		t.Fatal("0 should be false")
	}
	if !getEnvBool("TEST_BOOL_UNSET", true) {
		t.Fatal("unset should use fallback")
	}
}

func TestParseDur(t *testing.T) {
	if got := ParseDur("5s", 0); got.Seconds() != 5 {
		t.Fatalf("got %v", got)
	}
	if got := ParseDur("garbage", 42); got != 42 {
		t.Fatalf("bad input should fall back, got %v", got)
	}
	if got := ParseDur("", 7); got != 7 {
		t.Fatalf("empty should fall back, got %v", got)
	}
}
