-- ===== 007_data_scope_and_turnover.sql =====
-- Issue #13：股票池扩至全量 A 股（排除北交所）+ 换手率入库。
--
-- 背景：系统此前只维护沪深300+中证500 ≈ 800 只的行情/估值/因子/信号，而
--       stock_basic 已登记全市场。本迁移把「采集范围」与「策略选股域」拆成两列：
--         universe   —— 保持原样（hs300/zz500），只给策略链路看（factor_service.pool_codes
--                       / market_ready / replay.preload），动它就是换策略定义；
--         data_scope —— 新增，标记「采集哪些股」。'a_share' = market IN ('SH','SZ')
--                       （2315+2897=5212，排除北交所 338 与 INDEX 4），NULL = 不采集。
--       采集侧过滤点改读 data_scope，策略侧一律不动 → factor_value 域不变，
--       历史回测/绩效序列仍可比。
--
-- 换手率：daily_price 新增 turnover_rate（%）。口径**百分数**（0.21 = 0.21%），
--         来自腾讯日K [7] 与批量快照 [38]；新浪 turnover 是小数（0.0021）不可混用，
--         故只允许腾讯源写该列（BaoStock 兜底腿留空）。
--
-- 索引：daily_price 此前只有 (code, trade_date) 复合索引，data_quality._market_latest
--       以 trade_date 做 max() 只能 seq scan（每次体检调 7 次）。补 trade_date 单列索引。
--
-- 幂等：全部 ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / 无条件重写，
--       生产旧库由本脚本补齐（init.sql 只在首次初始化建表）。
-- 对应代码：collector/app/collectors/scope.py、collectors/daily.py、sources/tencent.py、
--           quant-engine/app/data_quality.py。

-- 1. 采集范围列
ALTER TABLE stock_basic ADD COLUMN IF NOT EXISTS data_scope VARCHAR(16);

-- 2. 按市场重标（无条件重写：幂等，且顺带自愈任何误标）
UPDATE stock_basic
   SET data_scope = CASE WHEN market IN ('SH', 'SZ') THEN 'a_share' ELSE NULL END;

CREATE INDEX IF NOT EXISTS idx_stock_basic_data_scope ON stock_basic (data_scope);

-- 3. 换手率列（%，腾讯日K[7] / 快照[38]；BaoStock·新浪腿不写）
ALTER TABLE daily_price ADD COLUMN IF NOT EXISTS turnover_rate DECIMAL(10,4);
COMMENT ON COLUMN daily_price.turnover_rate IS
    '换手率（%），腾讯日K[7]/快照[38]；BaoStock/新浪腿不写';

-- 4. trade_date 单列索引（_market_latest 的 max() 用；原复合索引前缀是 code）
CREATE INDEX IF NOT EXISTS idx_daily_price_trade_date ON daily_price (trade_date);
