package api

import (
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"quant-system/backend/internal/api/handler"
	"quant-system/backend/internal/repository"
	"quant-system/backend/internal/service"
)

// SetupRouter 注册全部路由
func SetupRouter(db *gorm.DB, tradingSvc *service.TradingService,
	navSvc *service.NavService, initialCash float64,
	taskRunSvc *service.TaskRunService, notifySvc *service.NotifyService,
	executeSvc *service.ExecuteService,
	briefSvc *service.MorningBriefService,
	llmSvc *service.LLMService) *gin.Engine {
	r := gin.New()
	r.Use(gin.Logger(), gin.Recovery())

	stockRepo := repository.NewStockRepository(db)
	dailyRepo := repository.NewDailyRepository(db)
	financialRepo := repository.NewFinancialRepository(db)
	accountRepo := repository.NewAccountRepository(db)
	orderRepo := repository.NewOrderRepository(db)
	signalRepo := repository.NewSignalRepository(db)
	positionRepo := repository.NewPositionRepository(db)
	tradeRepo := repository.NewTradeRepository(db)
	backtestRepo := repository.NewBacktestRepository(db)
	strategyRepo := repository.NewStrategyRepository(db)
	backtestSvc := service.NewBacktestService(backtestRepo, strategyRepo)
	strategySvc := service.NewStrategyService(strategyRepo, backtestSvc)
	tushareSvc := service.NewTushareConfigService(db)
	marketSvc := service.NewMarketStatusService(db)

	// 基础路径 /api/v1
	v1 := r.Group("/api/v1")
	{
		v1.GET("/health", handler.HealthCheck(db))
		v1.GET("/health/checks", handler.GetHealthChecks(taskRunSvc))
		v1.GET("/health/services", handler.GetServices(db))
		v1.GET("/health/data-assets", handler.GetDataAssets(db))

		// 股票相关
		v1.GET("/stocks", handler.GetStockList(stockRepo, signalRepo))
		v1.GET("/stocks/:code", handler.GetStockDetail(stockRepo, dailyRepo, financialRepo, signalRepo))
		v1.GET("/stocks/:code/financial", handler.GetFinancialList(financialRepo, stockRepo))
		v1.GET("/kline/:code", handler.GetKline(dailyRepo, stockRepo))

		// 策略与信号（Sprint 4；Iteration 4：策略生命周期 CRUD/switch/fork + 因子列表）
		v1.GET("/strategies", handler.GetStrategies(strategySvc))
		v1.POST("/strategies", handler.CreateStrategy(strategySvc))
		v1.PUT("/strategies/:name", handler.UpdateStrategy(strategySvc))
		v1.POST("/strategies/:name/versions", handler.ForkStrategy(strategySvc))
		v1.POST("/strategies/:name/switch", handler.SwitchStrategy(strategySvc))
		v1.DELETE("/strategies/:name", handler.DeleteStrategy(strategySvc))   // 第一轮测试：#2 补删除入口
		v1.GET("/strategies/compare", handler.CompareStrategies(strategySvc)) // §3.3 A/B
		v1.GET("/factors", handler.GetFactors(strategyRepo))
		v1.GET("/signals", handler.GetSignals(signalRepo))
		v1.GET("/signals/:code", handler.GetSignalsByCode(signalRepo, stockRepo))

		// 模拟交易（Sprint 5）
		v1.GET("/account", handler.GetAccount(accountRepo, initialCash))
		v1.GET("/account/nav", handler.GetAccountNav(navSvc, accountRepo))
		v1.GET("/positions", handler.GetPositions(positionRepo, accountRepo, stockRepo))
		v1.GET("/orders", handler.GetOrders(orderRepo, accountRepo, stockRepo))
		v1.POST("/orders", handler.PlaceOrder(tradingSvc, accountRepo))
		v1.DELETE("/orders/:id", handler.CancelOrder(tradingSvc, accountRepo))
		v1.GET("/trades", handler.GetTrades(tradeRepo, accountRepo, stockRepo))

		// 市场状态 + 指数基准（右上角开市/休市 chip + 行情概览；Sprint 6 指数）
		v1.GET("/market/status", handler.GetMarketStatus(marketSvc))
		v1.GET("/index/nav/:code", handler.GetIndexNav(dailyRepo))
		v1.GET("/index/quotes", handler.GetIndexQuotes(dailyRepo))
		v1.GET("/backtests", handler.GetBacktests(backtestSvc))
		v1.POST("/backtests", handler.CreateBacktest(backtestSvc))
		v1.GET("/backtests/:id", handler.GetBacktestDetail(backtestSvc))

		// 通知与任务监控（Issue #5）
		v1.GET("/notify/config", handler.GetNotifyConfig(notifySvc))
		v1.PUT("/notify/config/:event", handler.UpdateNotifyEvent(notifySvc))
		v1.PUT("/notify/config/feishu", handler.UpdateFeishuConfig(notifySvc))
		v1.POST("/notify/test", handler.SendTestCard(notifySvc))
		v1.GET("/tasks/runs", handler.GetTaskRuns(taskRunSvc))
		v1.POST("/trading/execute-day", handler.ManualExecuteDay(executeSvc))

		// 早盘简报（Issue #4）
		v1.GET("/morning-brief", handler.GetMorningBrief(briefSvc))

		// 数据源配置（Tushare token，页面可改）
		v1.GET("/config/tushare", handler.GetTushareConfig(tushareSvc))
		v1.PUT("/config/tushare", handler.UpdateTushareConfig(tushareSvc))
		v1.POST("/config/tushare/test", handler.TestTushare(tushareSvc))

		// 大模型能力（LLM，云端 API，只读白名单数据入口）
		v1.GET("/config/llm", handler.GetLLMConfig(llmSvc))
		v1.PUT("/config/llm", handler.UpdateLLMConfig(llmSvc))
		v1.POST("/config/llm/test", handler.TestLLM(llmSvc))
		v1.POST("/llm/glossary", handler.ExplainTerm(llmSvc))
		v1.POST("/llm/ask", handler.AskProject(llmSvc))
		v1.POST("/llm/interpret-brief", handler.InterpretBrief(llmSvc))
	}

	return r
}
