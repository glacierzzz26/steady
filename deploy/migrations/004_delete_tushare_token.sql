-- 阶段 3 去 Tushare 依赖：删除已无消费者的配置键（幂等，键不存在时无操作）
DELETE FROM app_config WHERE key = 'tushare.token';
