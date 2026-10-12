// Package datahub 是 Phase 3 读切换的**唯一出网点**（Go 侧）：
// 对 datahub 服务的 HTTP 读取集中在此。语义对齐 quant-engine `app/datahub_client.py`。
//
// - Bearer 鉴权；信封 {code,message,data,meta} → data；code!=0 视为解析错误。
// - 重试只对瞬时故障（超时/连接错/5xx）；4xx 快速失败（含 401，fail-closed）。
// - 显式超时；进程内短 TTL 缓存（降低读放大）。
// - 显式构造 + 注入（不设包级全局）：便于测试并行、避免隐藏状态。
package datahub

import "fmt"

// HTTPError 非 2xx 响应（4xx 快速失败；5xx 重试耗尽后抛出）。
type HTTPError struct {
	StatusCode int
	URL        string
	Body       string
}

func (e *HTTPError) Error() string {
	if e.StatusCode == 0 {
		return fmt.Sprintf("datahub 请求未完成: %s", e.URL)
	}
	return fmt.Sprintf("datahub 返回 %d: %s: %s", e.StatusCode, e.URL, e.Body)
}

// TimeoutError 连接/读超时或连接错误（重试耗尽后抛出）。
type TimeoutError struct {
	URL string
	Err error
}

func (e *TimeoutError) Error() string {
	return fmt.Sprintf("datahub 请求超时/连接失败: %s: %v", e.URL, e.Err)
}

func (e *TimeoutError) Unwrap() error { return e.Err }

// ParseError 响应非 JSON、信封 code!=0、或 data 非列表。
type ParseError struct{ Msg string }

func (e *ParseError) Error() string { return e.Msg }
