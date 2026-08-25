-- ===== 002_factor_research.sql =====
-- 2.3 因子研究闭环：factor_definition 加版本/状态；新建 factor_stat / factor_corr。
--
-- 背景：生产旧库由 init.sql 首次初始化，升级不复跑；此迁移幂等可重放，
--       可安全运行在「全新库已含新列/新表」或「手工已补」的数据库上。
-- 对应代码：quant-engine/app/factor_research.py（预计算）、
--           backend/internal/model/strategy.go（FactorDefinition.Version/Status）
-- 对应 init.sql：deploy/postgres/init.sql 中 factor_definition 定义。

-- 1. factor_definition 增加版本与状态（状态机：draft/trial/verified/active/disabled）
ALTER TABLE factor_definition
    ADD COLUMN IF NOT EXISTS version VARCHAR(20) NOT NULL DEFAULT 'v1.0';
ALTER TABLE factor_definition
    ADD COLUMN IF NOT EXISTS status  VARCHAR(10) NOT NULL DEFAULT 'active';

-- 2. factor_stat：因子检验统计（per-date 追加，quant-engine 预计算 / Go 读取聚合）
--    IC 系列（1/5/10/20/60 日前向收益的横截面 Rank IC）+ 5 分层组均前向收益（H=5）。
CREATE TABLE IF NOT EXISTS factor_stat (
    id          BIGSERIAL     PRIMARY KEY,
    factor_name VARCHAR(50)   NOT NULL REFERENCES factor_definition (name),
    trade_date  DATE          NOT NULL,
    ic_1d       DECIMAL(12,6),   -- T→T+1 横截面 Rank IC
    ic_5d       DECIMAL(12,6),   -- T→T+5
    ic_10d      DECIMAL(12,6),
    ic_20d      DECIMAL(12,6),
    ic_60d      DECIMAL(12,6),
    q1          DECIMAL(12,6),   -- 当日 5 分层组均前向收益（H=5，Q1=因子最优组）
    q2          DECIMAL(12,6),
    q3          DECIMAL(12,6),
    q4          DECIMAL(12,6),
    q5          DECIMAL(12,6),
    created_at  TIMESTAMP     DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_factor_stat
    ON factor_stat (factor_name, trade_date);

-- 3. factor_corr：6 因子两两相关性矩阵（per-date，Go 读区间做矩阵平均）
CREATE TABLE IF NOT EXISTS factor_corr (
    id          BIGSERIAL     PRIMARY KEY,
    trade_date  DATE          NOT NULL UNIQUE,
    matrix      JSONB         NOT NULL,   -- [[...6x6...]]
    created_at  TIMESTAMP     DEFAULT NOW()
);
