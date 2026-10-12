package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/viper"
)

// ServerConfig HTTP 服务配置
type ServerConfig struct {
	Host         string `mapstructure:"host"`
	Port         int    `mapstructure:"port"`
	ReadTimeout  string `mapstructure:"read_timeout"`
	WriteTimeout string `mapstructure:"write_timeout"`
}

// DatabaseConfig 数据库配置
type DatabaseConfig struct {
	Host     string `mapstructure:"host"`
	Port     int    `mapstructure:"port"`
	User     string `mapstructure:"user"`
	Password string `mapstructure:"password"`
	Name     string `mapstructure:"name"`
	MaxConns int    `mapstructure:"max_conns"`
	MaxIdle  int    `mapstructure:"max_idle"`
}

// LogConfig 日志配置
type LogConfig struct {
	Level      string `mapstructure:"level"`
	Format     string `mapstructure:"format"`
	MaxSize    int    `mapstructure:"max_size"`
	MaxBackups int    `mapstructure:"max_backups"`
	MaxAge     int    `mapstructure:"max_age"`
}

// AccountConfig 模拟交易费用配置（与 quant-engine 共用同一份数值，禁止单独改动）
type AccountConfig struct {
	InitialCash    float64 `mapstructure:"initial_cash"`
	CommissionRate float64 `mapstructure:"commission_rate"`
	MinCommission  float64 `mapstructure:"min_commission"`
	StampTaxRate   float64 `mapstructure:"stamp_tax_rate"`
	Slippage       float64 `mapstructure:"slippage"`
}

// DatahubConfig datahub 读取层配置（Phase 3 读切换）。
//
// 与 quant-engine 侧同源：env 名一致（DATAHUB_*），两容器共享同一份 .env。
// **默认全关**：read_datasets 空 ⇒ 所有读走本地库 ⇒ 部署零行为变更。
// 翻闸 = 改 .env 的 DATAHUB_READ_DATASETS 后重启（启动时读一次即可）。
type DatahubConfig struct {
	BaseURL        string   `mapstructure:"base_url"`      // 默认 http://datahub:8100
	Token          string   `mapstructure:"token"`         // 空 ⇒ 恒 disabled（fail-closed）
	ReadDatasets   []string `mapstructure:"read_datasets"` // 值 = dataset id（如 stock_basic）
	ConnectTimeout string   `mapstructure:"connect_timeout"`
	ReadTimeout    string   `mapstructure:"read_timeout"`
	Retries        int      `mapstructure:"retries"`
	RetryDelay     string   `mapstructure:"retry_delay"`
	CacheTTL       string   `mapstructure:"cache_ttl"` // "0s" = 关缓存
	FallbackLocal  bool     `mapstructure:"fallback_local"`
}

// ReadEnabled 判定某数据集是否走 datahub：
// base_url 非空 × token 非空 × dataset ∈ ReadDatasets（三条件同时满足）。
// 镜像 quant-engine `config.datahub_read_enabled`。
func (c DatahubConfig) ReadEnabled(dataset string) bool {
	if strings.TrimSpace(c.BaseURL) == "" || strings.TrimSpace(c.Token) == "" {
		return false
	}
	for _, d := range c.ReadDatasets {
		if strings.TrimSpace(d) == dataset {
			return true
		}
	}
	return false
}

// Config 总配置
type Config struct {
	Server   ServerConfig   `mapstructure:"server"`
	Database DatabaseConfig `mapstructure:"database"`
	Log      LogConfig      `mapstructure:"log"`
	Account  AccountConfig  `mapstructure:"account"`
	Datahub  DatahubConfig  `mapstructure:"datahub"`
}

// Load 加载配置文件，敏感项优先取环境变量（DB_PASSWORD 等）
func Load() (*Config, error) {
	v := viper.New()
	v.SetConfigName("config")
	v.SetConfigType("yaml")

	// 容器内配置路径 /app/configs，本地开发用 ./configs
	for _, dir := range []string{"/app/configs", "configs"} {
		if _, err := os.Stat(filepath.Join(dir, "config.yaml")); err == nil {
			v.AddConfigPath(dir)
			break
		}
	}

	if err := v.ReadInConfig(); err != nil {
		return nil, fmt.Errorf("读取配置文件失败: %w", err)
	}

	cfg := &Config{}
	if err := v.Unmarshal(cfg); err != nil {
		return nil, fmt.Errorf("解析配置文件失败: %w", err)
	}

	// 环境变量覆盖（Docker Compose 注入）
	cfg.Database.Host = getEnv("DB_HOST", cfg.Database.Host)
	cfg.Database.Port = getEnvInt("DB_PORT", cfg.Database.Port)
	cfg.Database.User = getEnv("DB_USER", cfg.Database.User)
	cfg.Database.Password = getEnv("DB_PASSWORD", cfg.Database.Password)
	cfg.Database.Name = getEnv("DB_NAME", cfg.Database.Name)

	// datahub 读取层（Phase 3）：env 名与 quant-engine 一致，两容器共享 .env
	cfg.Datahub.BaseURL = getEnv("DATAHUB_BASE_URL", cfg.Datahub.BaseURL)
	cfg.Datahub.Token = getEnv("DATAHUB_TOKEN", cfg.Datahub.Token)
	cfg.Datahub.ReadDatasets = getEnvList("DATAHUB_READ_DATASETS", cfg.Datahub.ReadDatasets)
	cfg.Datahub.ConnectTimeout = getEnv("DATAHUB_HTTP_CONNECT_TIMEOUT", cfg.Datahub.ConnectTimeout)
	cfg.Datahub.ReadTimeout = getEnv("DATAHUB_HTTP_READ_TIMEOUT", cfg.Datahub.ReadTimeout)
	cfg.Datahub.Retries = getEnvInt("DATAHUB_RETRIES", cfg.Datahub.Retries)
	cfg.Datahub.RetryDelay = getEnv("DATAHUB_RETRY_DELAY", cfg.Datahub.RetryDelay)
	cfg.Datahub.CacheTTL = getEnv("DATAHUB_CACHE_TTL", cfg.Datahub.CacheTTL)
	cfg.Datahub.FallbackLocal = getEnvBool("DATAHUB_FALLBACK_LOCAL", cfg.Datahub.FallbackLocal)

	return cfg, nil
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getEnvInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		var n int
		if _, err := fmt.Sscanf(v, "%d", &n); err == nil {
			return n
		}
	}
	return fallback
}

// getEnvList 逗号分隔 env → 去空字符串切片；未设/空取 fallback
func getEnvList(key string, fallback []string) []string {
	raw := os.Getenv(key)
	if strings.TrimSpace(raw) == "" {
		return fallback
	}
	out := make([]string, 0)
	for _, s := range strings.Split(raw, ",") {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// getEnvBool 布尔 env（1/true/yes/on，不区分大小写）；未设取 fallback。镜像 Python _bool
func getEnvBool(key string, fallback bool) bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(key)))
	if v == "" {
		return fallback
	}
	return v == "1" || v == "true" || v == "yes" || v == "on"
}

// ParseDur 解析时长字符串（如 "5s"/"500ms"）；空/非法回退 def。
// 供 datahub 客户端把 config 里的 string 时长转 time.Duration。
func ParseDur(s string, def time.Duration) time.Duration {
	s = strings.TrimSpace(s)
	if s == "" {
		return def
	}
	d, err := time.ParseDuration(s)
	if err != nil {
		return def
	}
	return d
}
