package service

import "strings"

// maskToken 密钥脱敏：只回显后 4 位（"****abcd"；空/短值显示 "****"）。
// 原由 TushareConfigService 使用；阶段 3 去 Tushare 后仅 llm.go 消费，保留为共享助手。
func maskToken(t string) string {
	t = strings.TrimSpace(t)
	if len(t) <= 4 {
		return "****"
	}
	return "****" + t[len(t)-4:]
}
