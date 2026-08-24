-- ===== 001_backtest_fill_mode.sql =====
-- Iteration 3 回测可信度校准：backtest_job 增加成交假设字段 fill_mode。
--
-- 背景：生产旧库由 init.sql 首次初始化，升级不复跑；此迁移幂等可重放，
--       可安全运行在「全新库已含 fill_mode」或「手工已补列」的数据库上。
-- 对应代码：backend/internal/model/backtest.go（BacktestJob.FillMode）
-- 对应 init.sql：deploy/postgres/init.sql 中 backtest_job 定义。

ALTER TABLE backtest_job ADD COLUMN IF NOT EXISTS fill_mode VARCHAR(16) NOT NULL DEFAULT 't_close';

-- 唯一索引纳入 fill_mode（旧库索引缺该列，会破坏后端 ON CONFLICT (…, fill_mode) 匹配）
DROP INDEX IF EXISTS uq_backtest_job;
CREATE UNIQUE INDEX uq_backtest_job
    ON backtest_job (strategy_name, start_date, end_date, top_n, fill_mode);
