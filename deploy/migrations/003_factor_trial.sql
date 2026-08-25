-- ===== 003_factor_trial.sql =====
-- 2.3b FactorFactory（G10）：factor_definition 加 params 参数快照；新建 factor_trial 试算任务表（DB 队列）。
--
-- 背景：生产旧库由 init.sql 首次初始化，升级不复跑；此迁移幂等可重放。
-- 设计：
--   - factor_definition.params 存变体因子参数（如 ma_trend 窗口、MACD 快慢线），
--     契约 §6.2 fork「含 params 快照」落在此列；试算具体参数存 factor_trial.params。
--   - factor_trial 对齐 backtest_job 模式——Go 提交 pending → quant-engine 每 5 分钟消费
--     （pending→running→done/failed），写 result JSONB。
-- 对应代码：backend/internal/model/strategy.go（FactorDefinition.Params、FactorTrial）、
--           quant-engine/app/factor_service.py（trial 消费，2.3b-4 实现）。
-- 对应 init.sql：deploy/postgres/init.sql 4/5.2 节。

-- 1. factor_definition 增加参数快照（G10 变体因子参数；v1.0 为 NULL）
ALTER TABLE factor_definition
    ADD COLUMN IF NOT EXISTS params JSONB;

-- 2. factor_trial：因子试算/寻优任务
--    params JSONB 存储试算参数（trial: {"start","end",...单组参数}；
--    optimize: {"start","end","param_grid":{...}}）；
--    result JSONB 存储结果（trial: {"ic_series","icir","quantiles","monotonic","heatmap"?}；
--    optimize: {"windows","horizons","grid"}）。kind 由 params 是否含 param_grid 区分，不加列。
CREATE TABLE IF NOT EXISTS factor_trial (
    id          BIGSERIAL     PRIMARY KEY,
    factor_name VARCHAR(50)   NOT NULL REFERENCES factor_definition (name),
    params      JSONB,                          -- 试算参数（窗口/持有期网格等）
    status      VARCHAR(16)   NOT NULL DEFAULT 'pending',  -- pending/running/done/failed
    result      JSONB,                          -- IC/ICIR/分层单调性/热力图数据
    error       TEXT,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

-- 消费队列按 status 轮询 pending；列表按 factor 展示
CREATE INDEX IF NOT EXISTS idx_factor_trial_status ON factor_trial (status);
CREATE INDEX IF NOT EXISTS idx_factor_trial_factor ON factor_trial (factor_name);
