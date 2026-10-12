package datahub

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"

	"quant-system/backend/internal/config"
)

// retryStatus 可重试的瞬时 HTTP 状态码（4xx 一律快速失败）。
var retryStatus = map[int]bool{500: true, 502: true, 503: true, 504: true}

type cacheEntry struct {
	at  time.Time
	raw json.RawMessage
}

// Client datahub HTTP 读客户端（唯一出网点）。并发安全。
type Client struct {
	baseURL     string
	token       string
	hc          *http.Client
	readTimeout time.Duration
	retries     int
	retryDelay  time.Duration
	cacheTTL    time.Duration

	mu    sync.Mutex
	cache map[string]cacheEntry
}

// New 构造客户端。连接超时走 Transport.DialContext；读超时走每请求 context。
func New(cfg config.DatahubConfig) *Client {
	connect := config.ParseDur(cfg.ConnectTimeout, 5*time.Second)
	tr := &http.Transport{
		DialContext: (&net.Dialer{Timeout: connect}).DialContext,
	}
	return &Client{
		baseURL:     strings.TrimRight(cfg.BaseURL, "/"),
		token:       cfg.Token,
		hc:          &http.Client{Transport: tr},
		readTimeout: config.ParseDur(cfg.ReadTimeout, 10*time.Second),
		retries:     max(0, cfg.Retries),
		retryDelay:  config.ParseDur(cfg.RetryDelay, 500*time.Millisecond),
		cacheTTL:    config.ParseDur(cfg.CacheTTL, 60*time.Second),
		cache:       map[string]cacheEntry{},
	}
}

// Reset 清空 TTL 缓存（测试用，防跨用例串味）。
func (c *Client) Reset() {
	c.mu.Lock()
	c.cache = map[string]cacheEntry{}
	c.mu.Unlock()
}

// FetchRaw 取 datasetID，返回 data 数组的原始 JSON。唯一出网点。
func (c *Client) FetchRaw(ctx context.Context, datasetID string, params map[string]string) (json.RawMessage, error) {
	if c.baseURL == "" || c.token == "" {
		return nil, &ParseError{Msg: "datahub 客户端未配置（base_url/token 为空）"}
	}
	u := c.baseURL + "/v1/datasets/" + datasetID
	key := u + "?" + canonical(params)
	if raw, ok := c.cacheGet(key); ok {
		return raw, nil
	}
	raw, err := c.request(ctx, u, params)
	if err != nil {
		return nil, err
	}
	c.cachePut(key, raw)
	return raw, nil
}

// Fetch 泛型解码：FetchRaw → json.Unmarshal 进 []T（T = 调用方的 wire DTO）。
// Go 不支持泛型方法，故为包级函数。
func Fetch[T any](c *Client, ctx context.Context, datasetID string, params map[string]string) ([]T, error) {
	raw, err := c.FetchRaw(ctx, datasetID, params)
	if err != nil {
		return nil, err
	}
	var out []T
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, &ParseError{Msg: "datahub data 解码失败: " + err.Error()}
	}
	return out, nil
}

func (c *Client) request(ctx context.Context, u string, params map[string]string) (json.RawMessage, error) {
	q := url.Values{}
	for k, v := range params {
		q.Set(k, v)
	}
	full := u
	if enc := q.Encode(); enc != "" {
		full = u + "?" + enc
	}

	for attempt := 0; attempt <= c.retries; attempt++ {
		last := attempt == c.retries
		raw, status, retryable, err := c.doOnce(ctx, full)
		if err != nil {
			// 连接错/超时：镜像 Python——重试，耗尽后抛 TimeoutError
			if last {
				return nil, &TimeoutError{URL: u, Err: err}
			}
			time.Sleep(c.retryDelay)
			continue
		}
		if retryable {
			if last {
				return nil, &HTTPError{StatusCode: status, URL: u, Body: truncate(raw)}
			}
			time.Sleep(c.retryDelay)
			continue
		}
		if status >= 400 {
			// 4xx：快速失败（fail-closed）
			return nil, &HTTPError{StatusCode: status, URL: u, Body: truncate(raw)}
		}
		return parseEnvelope(raw)
	}
	return nil, &HTTPError{URL: u} // 不可达
}

// doOnce 单次请求。返回 (body, statusCode, retryable, err)。
// err 非空 = 传输层失败（无响应）；retryable 仅在拿到响应时有意义。
func (c *Client) doOnce(ctx context.Context, full string) ([]byte, int, bool, error) {
	reqCtx, cancel := context.WithTimeout(ctx, c.readTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, full, nil)
	if err != nil {
		return nil, 0, false, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.hc.Do(req)
	if err != nil {
		return nil, 0, false, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, 0, false, err
	}
	return body, resp.StatusCode, retryStatus[resp.StatusCode], nil
}

func parseEnvelope(body []byte) (json.RawMessage, error) {
	var env struct {
		Code    int             `json:"code"`
		Message string          `json:"message"`
		Data    json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(body, &env); err != nil {
		return nil, &ParseError{Msg: "datahub 响应非 JSON: " + truncate(body)}
	}
	if env.Code != 0 {
		return nil, &ParseError{Msg: fmt.Sprintf("datahub 信封异常（code=%d）: %s", env.Code, env.Message)}
	}
	d := bytes.TrimSpace(env.Data)
	if len(d) == 0 || string(d) == "null" {
		return json.RawMessage("[]"), nil // 空结果是合法值（raw 数据集语义）
	}
	if d[0] != '[' {
		return nil, &ParseError{Msg: "datahub data 非列表"}
	}
	return json.RawMessage(append([]byte(nil), d...)), nil
}

// canonical 规范参数串（sorted k=v），作缓存 key。
func canonical(params map[string]string) string {
	keys := make([]string, 0, len(params))
	for k := range params {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for i, k := range keys {
		if i > 0 {
			b.WriteByte('&')
		}
		b.WriteString(url.QueryEscape(k))
		b.WriteByte('=')
		b.WriteString(url.QueryEscape(params[k]))
	}
	return b.String()
}

func (c *Client) cacheGet(key string) (json.RawMessage, bool) {
	if c.cacheTTL <= 0 {
		return nil, false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	e, ok := c.cache[key]
	if !ok || time.Since(e.at) >= c.cacheTTL {
		return nil, false
	}
	return e.raw, true
}

func (c *Client) cachePut(key string, raw json.RawMessage) {
	if c.cacheTTL <= 0 {
		return
	}
	c.mu.Lock()
	c.cache[key] = cacheEntry{at: time.Now(), raw: raw}
	c.mu.Unlock()
}

func truncate(b []byte) string {
	const n = 200
	if len(b) > n {
		return string(b[:n])
	}
	return string(b)
}
