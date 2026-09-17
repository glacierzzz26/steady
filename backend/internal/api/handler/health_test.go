package handler

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestHTTPProbe 锁住「非 2xx 不再算 ok」的语义（Issue #14）。
// 原实现「有响应即 ok」会把看门狗探到的 job 卡死（/healthz 答 503）藏成正常，
// 运维页因此看不见——这正是 09-08 采集卡死后无人知晓的一环。
func TestHTTPProbe(t *testing.T) {
	t.Run("2xx ok", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		}))
		defer srv.Close()

		status, detail := httpProbe(srv.URL)
		if status != "ok" {
			t.Fatalf("200 应为 ok: got %q (%s)", status, detail)
		}
	})

	t.Run("503 degraded 且带状态码与响应体", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = w.Write([]byte(`{"status":"stale","job":"job_sync_daily_price"}`))
		}))
		defer srv.Close()

		status, detail := httpProbe(srv.URL)
		if status != "degraded" {
			t.Fatalf("503 应为 degraded（非 ok）: got %q", status)
		}
		if !strings.Contains(detail, "503") {
			t.Fatalf("detail 应含状态码 503: got %q", detail)
		}
		if !strings.Contains(detail, "job_sync_daily_price") {
			t.Fatalf("detail 应含响应体原因: got %q", detail)
		}
	})

	t.Run("非 2xx 空响应体仍报状态码", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusInternalServerError)
		}))
		defer srv.Close()

		status, detail := httpProbe(srv.URL)
		if status != "degraded" {
			t.Fatalf("500 应为 degraded: got %q", status)
		}
		if !strings.Contains(detail, "500") {
			t.Fatalf("detail 应含状态码 500: got %q", detail)
		}
	})

	t.Run("响应体超长被截断", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = w.Write([]byte(strings.Repeat("x", 4096)))
		}))
		defer srv.Close()

		_, detail := httpProbe(srv.URL)
		if len(detail) > 512+128 { // 512 截断 + 「服务自报不健康（HTTP 503）：」前缀
			t.Fatalf("detail 未按 512 字节截断: len=%d", len(detail))
		}
	})

	t.Run("连接被拒 down", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {}))
		url := srv.URL
		srv.Close() // 关掉 → 端口无人监听

		status, _ := httpProbe(url)
		if status != "down" {
			t.Fatalf("连接被拒应为 down: got %q", status)
		}
	})
}
