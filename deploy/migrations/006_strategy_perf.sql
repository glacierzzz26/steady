-- ===== 006_strategy_perf.sql =====
-- 2.6 策略效果度量第一期（方向①）：strategy_perf 预计算绩效表（只读消费）。
--
-- 背景：系统已达「每天自动运行 + 主动通知 + 可追溯」，但缺「结果可解释」——
--       用户无法回答「策略是否有效、实盘与回测差多少」。本表承接 quant-engine
--       预计算（G9 同款模式：Python 算好落库，Go/前端只读），metric_type 分列：
--         hit_rate    信号命中率（BUY 样本 forward 5/10/20d 收益 + 相对基准命中）
--         nav_overlay 实盘(account_nav) vs 回测(t1_open backtest nav) vs 基准(sh000300)
--       幂等：UNIQUE(strategy_name, period_end, metric_type) + upsert，每日 21:20 覆盖重算。
-- 生产旧库由 init.sql 首次初始化，升级不复跑；此迁移幂等可重放。
-- 对应代码：quant-engine/app/performance.py、backend handler/performance.go、frontend /performance。

CREATE TABLE IF NOT EXISTS strategy_perf (
    id            BIGSERIAL     PRIMARY KEY,
    strategy_name VARCHAR(50)   NOT NULL,                    -- 策略名（默认 'multi_factor'）
    period_start  DATE          NOT NULL,                    -- 统计起始日
    period_end    DATE          NOT NULL,                    -- 统计截止日（hit_rate=end_date；nav_overlay=今天）
    metric_type   VARCHAR(20)   NOT NULL,                    -- hit_rate / nav_overlay（attribution 第二期）
    detail        JSONB,                                     -- {windows:{5:{...}}, series:[{date,live,bt,benchmark}], ...}
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_strategy_perf UNIQUE (strategy_name, period_end, metric_type)
);

-- 月度绩效报告事件配置（事件型，页面可控开关；幂等，对齐 init.sql 通知种子段）
INSERT INTO notify_config (event_key, name, enabled, schedule_type, weekdays, send_at, template)
VALUES ('perf_report', '月度绩效报告', TRUE, 'event', NULL, NULL, 'blue')
ON CONFLICT (event_key) DO NOTHING;
