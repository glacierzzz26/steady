/**
 * API 类型 —— 形状对齐后端真实 DTO（契约 `frontend-v2-api-contract.md` §4）。
 * G 缺口字段一律 `?:`：后端补齐后前端零改动点亮；补齐前页面读到时走空态。
 * 信号 action / 股票池 universe 保持后端原样（大写），展示层经 `transform.ts` 转小写。
 */

// ============ 通用 ============
export interface ApiResponse<T> {
  code: number
  message: string
  data: T
  timestamp?: string
}

export type Market = 'SH' | 'SZ' | 'BJ'
export type Universe = 'hs300' | 'zz500'
export type Adjust = 'none' | 'qfq' | 'hfq'
export type SortField = 'code' | 'name' | 'list_date' | 'market' | 'industry'
export type SignalAction = 'BUY' | 'SELL' | 'HOLD'

// ============ 股票列表（契约 §4.1）============
export interface StockBasic {
  code: string
  name: string
  market: Market
  industry: string
  list_date: string // YYYY-MM-DD，缺失为空串
  status: string
  universe: string // hs300 / zz500 / ''（空串=全市场候选）
}

/** G2 扩展项：行情/估值/评分/信号（后端缺口补齐前可空） */
export interface StockPoolItem extends StockBasic {
  price?: number // 最新价
  chg?: number // 涨跌幅 %
  amount?: number // 成交额（元）
  pe?: number // PE(TTM)
  pb?: number
  roe?: number // %
  score?: number // 综合分
  rank?: number // 横截面排名
  signal?: SignalAction
}

export interface StockListData {
  total: number
  page: number
  page_size: number
  items: StockPoolItem[]
}

export interface StockListQuery {
  page?: number
  page_size?: number
  industry?: string
  keyword?: string
  market?: Market
  universe?: Universe
  sort?: SortField
  order?: 'asc' | 'desc'
}

// ============ K 线（契约 §4.3）============
export interface KLineItem {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number // 单位：手
  amount: number // 单位：元
}

export interface KLineData {
  code: string
  period: string
  adjust: string
  items: KLineItem[]
}

// ============ 财务（契约 §4.4）============
export interface FinancialItem {
  report_date: string // YYYY-MM-DD
  announce_date: string
  pe: number
  pb: number
  roe: number // 以下 5 个为原始百分数值（15.2 = 15.2%），0 视为缺失
  profit_growth: number
  revenue_growth: number
  debt_ratio: number
  gross_margin: number
}

export interface FinancialListData {
  code: string
  items: FinancialItem[]
}

export interface Valuation {
  trade_date: string
  pe_ttm: number
  pb: number
}

// ============ 个股详情（契约 §4.2）============
/** G3 扩展：因子得分（综合分/排名/信号/四维），后端补齐前可空 */
export interface FactorScore {
  score?: number
  rank?: number
  signal?: SignalAction
  trend?: number
  value?: number
  quality?: number
  risk?: number
}

export interface StockDetail extends StockBasic {
  latest_bar: KLineItem | null
  financial_summary: FinancialItem | null
  valuation: Valuation | null
  factor_score?: FactorScore | null
}

// ============ 模拟交易（契约 §4.8）============
export interface AccountData {
  account_id: number
  name: string
  cash: number
  market_value: number
  total_asset: number
  profit: number
  profit_rate: number
  max_drawdown: number
  initial_cash: number
}

export interface AccountNavItem {
  trade_date: string
  total_asset: number
  nav: number
  daily_return: number
  drawdown: number
}

export interface AccountNavData {
  items: AccountNavItem[]
}

export interface PositionItem {
  code: string
  name: string
  quantity: number
  available_qty: number // < quantity 表示 T+1 冻结
  cost_price: number
  current_price: number
  market_value: number
  profit: number
  profit_rate: number
}

export interface PositionsData {
  items: PositionItem[]
}

export type OrderStatus = 'PENDING' | 'FILLED' | 'REJECTED' | 'CANCELLED'
export type OrderDirection = 'BUY' | 'SELL'

/** G4 扩展：name（股票名称，后端补齐前可空，页面用 `name || code` 兜底） */
export interface OrderItem {
  order_id: string
  code: string
  name?: string
  direction: OrderDirection
  order_type: string
  price: number
  quantity: number
  filled_qty: number
  avg_fill_price: number
  status: OrderStatus
  reason: string
  source: string
  created_at: string
}

export interface OrdersData {
  items: OrderItem[]
  total: number
  page: number
  page_size: number
}

export interface TradeItem {
  trade_id: string
  order_id: string
  code: string
  name?: string // G4
  direction: OrderDirection
  price: number
  quantity: number
  amount: number
  commission: number
  tax: number
  net_amount: number
  trade_date: string
}

export interface TradesData {
  items: TradeItem[]
  total: number
  page: number
  page_size: number
}

// ============ 信号（契约 §4.5）============
export interface StrategySignal {
  code: string
  name: string
  score: number
  action: SignalAction
  reason: string
  // G1 扩展：因子分项/行情（后端补齐前可空）
  rank?: number
  trend?: number
  value?: number
  quality?: number
  risk?: number
  pe?: number
  chg20?: number
}

export interface SignalsData {
  strategy: string
  trade_date: string // '' = 尚无信号
  items: StrategySignal[]
  total: number
  page: number
  page_size: number
}

export interface SignalQuery {
  strategy?: string
  date?: string
  action?: SignalAction
  page?: number
  page_size?: number
}

// ============ 策略定义（契约 §4.1/§4.2；Iteration 4 多策略生命周期）============
export interface StrategyInfo {
  name: string
  zh_name?: string
  description?: string
  version?: string
  status?: string // draft/backtest/sample/active/paused/archived
  factor_weights: Record<string, number>
  params: Record<string, unknown>
  latest_backtest_id?: number // 最近一次回测 job id（策略卡「回测依据」）
}

export interface StrategiesData {
  items: StrategyInfo[]
}

// 因子定义（构建器因子池：GET /factors 真实 factor_definition；2.3 补 version/status）
export interface FactorDefinition {
  name: string
  category?: string
  description?: string
  formula?: string
  weight: number
  version?: string
  status?: string // draft/trial/verified/active/disabled（仅 active 参与评分池）
}

export interface FactorsData {
  items: FactorDefinition[]
}

// ============ 因子研究（2.3 G9 FactorLab，契约《因子研究闭环》§6.1）============
export interface FactorStatPoint {
  date: string // YYYY-MM-DD
  ic: number | null // 所选 horizon 的当日 RankIC；尾部滞后为 null
}

export interface FactorDecayPoint {
  horizon: number
  ic: number | null
}

export interface FactorQuantile {
  group: number // 1=因子最优组
  ret: number | null // 组均前向收益（H=5）
}

/** GET /factors/:name/stats 响应 */
export interface FactorStatsData {
  factor: string
  category?: string
  range: { start: string; end: string; days: number }
  horizon: number
  ic_series: FactorStatPoint[]
  icir: number | null
  ic_mean: number | null
  ic_std: number | null
  ic_decay: FactorDecayPoint[]
  quantiles: FactorQuantile[]
  monotonic: number | null // Q1 跑赢 Q5 的交易日占比（0-1）
}

/** GET /factors/stats/correlation 响应 */
export interface FactorCorrData {
  factors: string[] // 6 因子规范序
  matrix: Array<Array<number | null>> // 6×6 区间均值，无共现格为 null
}

// A/B 对比（§3.3 GET /strategies/compare）
export interface CompareNavItem {
  date: string
  nav: number
}

export interface CompareSide {
  strategy_name: string
  total_return: number
  annualized_return: number
  max_drawdown: number
  sharpe: number
  turnover: number
  cost: number
  trades: number
  nav: CompareNavItem[]
}

export interface CompareBenchmark {
  code: string
  nav: CompareNavItem[]
}

export interface CompareResult {
  status?: 'pending' | 'done' | 'failed' // pending → 前端轮询
  base?: CompareSide
  candidate?: CompareSide
  benchmark?: CompareBenchmark
}

// ============ 个股信号历史（契约 §4.6）============
export interface SignalHistoryItem {
  trade_date: string
  score: number
  action: SignalAction
  reason: string
  rank?: number // G3
}

export interface SignalHistoryData {
  code: string
  items: SignalHistoryItem[]
}

// ============ 市场状态（GET /market/status，右上角开市/休市 chip）============
export interface MarketStatus {
  today: string // YYYY-MM-DD（CST）
  is_trade_day: boolean
  market_phase: 'pre_open' | 'open' | 'lunch_break' | 'closed' | 'off_day'
  phase_label: string // 未开盘 / 交易中 / 午间休市 / 已收盘 / 休市
  last_trade_date: string // 上一交易日
  next_trade_date: string // 下一交易日（>= today）
}

// ============ 指数基准 + 回测（契约 §4.9 / §4.10）============
export interface IndexNavItem {
  trade_date: string
  nav: number // 归一化净值（close/区间首日 close）
}

export interface IndexNavData {
  code: string
  items: IndexNavItem[]
}

// ============ 指数行情概览（GET /index/quotes，topbar 三枚指数芯片）============
export interface IndexQuote {
  code: string // sh000001 / sh000300 / sh000905
  name: string // 上证指数 / 沪深300 / 中证500
  close: number // 最新收盘
  change_pct: number // 较上一交易日涨跌幅（%）
  trade_date: string
}

export interface IndexQuotesData {
  items: IndexQuote[]
}

export type BacktestStatus = 'pending' | 'running' | 'done' | 'failed'

export interface BacktestNavItem {
  date: string
  nav: number
  benchmark: number | null
}

export interface BacktestJobItem {
  id: number
  strategy_name: string
  start_date: string
  end_date: string
  top_n: number
  status: BacktestStatus
  error: string
  created_at: string
  finished_at: string
  // 结果指标（done 后非零；收益为小数比例，展示 ×100）
  total_return: number
  annualized_return: number
  max_drawdown: number
  sharpe: number
  trading_days: number
  final_value: number
  trades: number
  positions: number
  benchmark_return: number
  excess_return: number
  nav: BacktestNavItem[] // 详情接口才有
  // G8 扩展：T+1 成交假设（t_close/t1_open）+ 年化偏差（2026-08-23 已点亮）
  fill_mode?: string
  t1_deviation?: number
  // Iteration 4 §3.5：年化单边换手（倍数/年）+ 年化交易成本占比（%）
  turnover?: number
  cost?: number
}

export interface BacktestsData {
  items: BacktestJobItem[]
}

export interface BacktestSubmit {
  start_date: string
  end_date: string
  top_n: number
  fill_mode?: string // G8：t_close/t1_open，前端默认切 t1_open
  strategy_name?: string // Iteration 4：指定策略回测（空=后端解析 active）
}

// ============ 通知配置（契约 §4.12）============
export type NotifyScheduleType = 'weekday' | 'trading_day' | 'event'

export interface NotifyEvent {
  event_key: string
  name: string
  enabled: boolean
  schedule_type: NotifyScheduleType
  weekdays: string // '1,2,3,4,5'（1=周一..7=周日）
  send_at: string | null // HH:MM；event 型为 null
  template: string
}

export interface FeishuConfig {
  enabled: boolean
  webhook_url: string
  dashboard_url: string
  timeout: number
  max_retries: number
  secret: string
  at_all: boolean
}

export interface NotifyConfigData {
  events: NotifyEvent[]
  feishu: FeishuConfig
}

// ============ 数据源配置（Tushare）============
export interface TushareConfig {
  configured: boolean
  token_masked: string
}

// ============ 大模型（LLM）============
export type LLMProvider = 'openai' | 'deepseek' | 'qwen' | 'glm'

export interface LLMConfig {
  enabled: boolean
  provider: LLMProvider
  model: string
  base_url: string
  api_key_masked: string
}

export interface LLMConfigUpdate {
  enabled: boolean
  provider: LLMProvider
  model: string
  base_url: string
  api_key?: string
  clear_api_key?: boolean
}

export interface TermExplanation {
  term: string
  explanation: string
}

export interface ProjectAnswer {
  question: string
  answer: string
}

export interface BriefInterpretation {
  brief_date: string
  interpretation: string
}

// ============ 任务运行（契约 §4.12）============
export type TaskRunStatus = 'success' | 'skipped' | 'failed'

export interface TaskRunItem {
  id: number
  task_name: string
  run_date: string
  status: TaskRunStatus
  message: string
  detail: Record<string, unknown> | null
  created_at: string
}

export interface TaskRunsData {
  items: TaskRunItem[]
}

// ============ 运维（G7，接口待建，类型先备）============
export interface ServiceStatus {
  name: string
  label: string
  status: 'ok' | 'down' | 'unknown'
  detail?: string
}

export interface DataAssetItem {
  table: string
  rows: number
}

export interface DataAssetData {
  items: DataAssetItem[]
}

// ============ 数据健康（G5，接口待建，类型先备）============
export interface HealthCheckItem {
  name: string
  value: string
  pct: number
  ok: boolean
}

export interface HealthChecksData {
  items: HealthCheckItem[]
  date?: string
}

// ============ /health（无信封，http.raw）============
export interface HealthData {
  status: string
  time: string
  db: 'ok' | string
}

// ============ 早盘简报（契约 §4.11，G6 字段核对）============
export interface MorningBriefIndexItem {
  name: string
  code: string
  close: number | null
  change_pct: number | null // 已是百分比数值（0.22 = 0.22%）
}

export interface MorningBriefSectorGainItem {
  name: string
  change_pct: number | null
  leader: string | null
}

export interface MorningBriefSectorFlowItem {
  name: string
  net_inflow: string | null // 已格式化字符串（"7.82亿"）
}

export interface MorningBriefHotStockItem {
  rank: number
  code: string
  name: string
  change_pct: number | null
  board_days?: number
  industry?: string | null
}

/** market 来自 collector JSONB，未产出时可能整体为空对象 → Partial */
export interface MorningBriefMarket {
  indices: MorningBriefIndexItem[]
  sectors_gain: MorningBriefSectorGainItem[]
  sectors_flow: MorningBriefSectorFlowItem[]
  hot_stocks: MorningBriefHotStockItem[]
}

export interface MorningBriefTradeOrder {
  code: string
  direction: OrderDirection
  price: number | null
  quantity: number
}

export interface MorningBriefYesterday {
  signal: { total: number; counts: Record<string, number>; top_buys: string[] }
  trade: {
    buy_count: number
    sell_count: number
    orders: MorningBriefTradeOrder[]
    message?: string
  }
  nav: { nav: number | null; daily_return: number | null; drawdown: number | null; total_asset: number | null }
  data_health: { overall: string; fail: number; warn: number; message: string }
  tasks: { task_name: string; status: string; message: string | null }[]
}

export interface MorningBriefToday {
  checklist: { time: string; task: string }[]
  positions: {
    code: string
    name: string
    quantity: number
    market_value: number | null
    profit_rate: number | null
  }[]
}

export interface MorningBriefSections {
  brief_date: string
  trade_date: string
  is_open_today: boolean
  market: Partial<MorningBriefMarket>
  yesterday: MorningBriefYesterday
  today: MorningBriefToday
}

export interface MorningBriefData {
  brief_date: string
  sections: MorningBriefSections
}
