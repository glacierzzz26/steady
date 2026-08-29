-- ===== 005_remediation_task.sql =====
-- 2.5 自愈能力（Issue #4）：remediation_task 自愈任务表（跨服务 DB 队列）。
--
-- 背景：2026-08-28 18:35 飞书收到「数据健康检查」红卡——行情覆盖 366/800（45.8%）
--       低于 90%，根因 BaoStock 匿名账号被限（10001011）。目标：异常卡发出后系统
--       自行 diff-repair → 复检 → 重算 → 回告绿卡，全程无需人工。
-- 生产旧库由 init.sql 首次初始化，升级不复跑；此迁移幂等可重放。
--
-- 队列模型（两段式交接，各守服务边界）：
--   producer（quant-engine job_data_quality 18:30，coverage fail 分支）→ pending
--   stage1（collector remediation.py，5min）→ repaired / source_blocked / failed
--   stage2（quant-engine job_consume_remediation，5min）→ done（复检绿 + 重算 + 绿卡）
--     / 仍红回 pending 再走 stage1，attempts≥3 → failed（红卡升级人工）
-- 去重：UNIQUE(trade_date, check_name) + ON CONFLICT DO NOTHING，多轮 18:30 重跑不重复插。
-- 对应代码：collector/app/remediation.py（stage1）、quant-engine/app/remediation.py（stage2）。
-- 对应 init.sql：deploy/postgres/init.sql 5.x 节。

CREATE TABLE IF NOT EXISTS remediation_task (
    id         BIGSERIAL     PRIMARY KEY,
    trade_date DATE          NOT NULL,                       -- 待修复的交易日
    check_name VARCHAR(32)   NOT NULL,                       -- 本期仅 'coverage'
    status     VARCHAR(16)   NOT NULL DEFAULT 'pending',   -- pending/repaired/done/failed/source_blocked
    attempts   INT           NOT NULL DEFAULT 0,           -- 已重试次数（上限 MAX_ATTEMPTS=3）
    detail     JSONB,                                        -- {missing_codes:[...], repaired_count, ...}
    created_at TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_remediation UNIQUE (trade_date, check_name)
);

-- 队列按 status 轮询
CREATE INDEX IF NOT EXISTS idx_remediation_status ON remediation_task (status);

-- 回告绿卡/红卡事件配置（事件型，页面可控开关；幂等，对齐 init.sql 通知种子段）
INSERT INTO notify_config (event_key, name, enabled, schedule_type, weekdays, send_at, template)
VALUES ('remedi', '自动修复', TRUE, 'event', NULL, NULL, 'green')
ON CONFLICT (event_key) DO NOTHING;
