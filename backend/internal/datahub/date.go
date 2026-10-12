package datahub

import (
	"fmt"
	"strings"
	"time"
)

// Date 解析 datahub 返回的日期字段（形如 "2026-10-09"）。
//
// 不能直接解进 model.* 的 time.Time（`encoding/json` 只认 RFC3339）。wire DTO 用本类型，
// 再转 model 需要的 time.Time。
type Date struct{ time.Time }

// UnmarshalJSON 兼容纯日期 "2006-01-02" 与 RFC3339。
func (d *Date) UnmarshalJSON(b []byte) error {
	s := strings.Trim(string(b), `"`)
	if s == "" || s == "null" {
		d.Time = time.Time{}
		return nil
	}
	for _, layout := range []string{"2006-01-02", time.RFC3339} {
		if t, err := time.Parse(layout, s); err == nil {
			d.Time = t
			return nil
		}
	}
	return fmt.Errorf("datahub 日期格式非法: %q", s)
}
