/**
 * 静态 mock 数据 —— 全部来自原型 index.html，
 * 仅作页面展示用，不接任何后端。
 */

export type SignalType = 'buy' | 'sell' | 'hold'
export type Board = 'hs' | 'zz'

export const SIGNAL_CN: Record<SignalType, string> = { buy: '买入', sell: '卖出', hold: '持有' }

export interface PoolStock {
  code: string
  name: string
  board: Board
  price: number
  chg: number      // 涨跌幅 %
  amount: number   // 成交额（亿）
  pe: number
  pb: number
  roe: number
  score: number    // 综合分
  rank: number     // 横截面排名
  signal: SignalType
}

/** 股票池 48 只（原型数据 1:1 迁移） */
export const pool: PoolStock[] = [
  { code: '601899', name: '紫金矿业', board: 'hs', price: 17.86, chg: 2.8, amount: 47.6, pe: 18.2, pb: 3.4, roe: 21.5, score: 78.6, rank: 4, signal: 'buy' },
  { code: '000333', name: '美的集团', board: 'hs', price: 68.42, chg: 1.2, amount: 21.3, pe: 11.6, pb: 2.2, roe: 22.4, score: 76.2, rank: 7, signal: 'buy' },
  { code: '600886', name: '国投电力', board: 'hs', price: 14.53, chg: 0.9, amount: 8.4, pe: 14.3, pb: 1.6, roe: 12.1, score: 75.1, rank: 9, signal: 'buy' },
  { code: '000792', name: '盐湖股份', board: 'hs', price: 28.84, chg: 4.2, amount: 58.2, pe: 22.7, pb: 4.1, roe: 12.8, score: 74.0, rank: 11, signal: 'buy' },
  { code: '601088', name: '中国神华', board: 'hs', price: 38.65, chg: 1.6, amount: 12.8, pe: 9.8, pb: 1.5, roe: 15.2, score: 73.4, rank: 13, signal: 'buy' },
  { code: '600900', name: '长江电力', board: 'hs', price: 27.31, chg: 0.4, amount: 9.1, pe: 19.5, pb: 3.2, roe: 16.8, score: 72.8, rank: 14, signal: 'buy' },
  { code: '601318', name: '中国平安', board: 'hs', price: 52.47, chg: 1.4, amount: 33.8, pe: 7.9, pb: 0.9, roe: 10.5, score: 72.1, rank: 15, signal: 'buy' },
  { code: '600028', name: '中国石化', board: 'hs', price: 6.35, chg: 0.6, amount: 7.2, pe: 8.7, pb: 0.8, roe: 8.9, score: 71.9, rank: 15, signal: 'buy' },
  { code: '002415', name: '海康威视', board: 'hs', price: 29.84, chg: -0.4, amount: 11.2, pe: 22.8, pb: 3.4, roe: 19.2, score: 49.6, rank: 41, signal: 'hold' },
  { code: '600519', name: '贵州茅台', board: 'hs', price: 1412.0, chg: 0.2, amount: 18.4, pe: 21.5, pb: 8.2, roe: 34.6, score: 61.5, rank: 18, signal: 'hold' },
  { code: '000651', name: '格力电器', board: 'hs', price: 41.28, chg: 0.5, amount: 9.4, pe: 8.4, pb: 2.1, roe: 24.2, score: 60.3, rank: 20, signal: 'hold' },
  { code: '600690', name: '海尔智家', board: 'hs', price: 28.16, chg: 1.0, amount: 7.8, pe: 13.2, pb: 1.8, roe: 17.4, score: 59.6, rank: 21, signal: 'hold' },
  { code: '600036', name: '招商银行', board: 'hs', price: 37.82, chg: 0.8, amount: 15.6, pe: 6.2, pb: 0.9, roe: 14.2, score: 58.9, rank: 22, signal: 'hold' },
  { code: '601728', name: '中国电信', board: 'hs', price: 7.24, chg: 0.4, amount: 5.6, pe: 16.8, pb: 1.3, roe: 7.8, score: 57.9, rank: 23, signal: 'hold' },
  { code: '601100', name: '恒立液压', board: 'zz', price: 52.18, chg: 1.9, amount: 5.6, pe: 24.6, pb: 4.8, roe: 18.6, score: 56.8, rank: 25, signal: 'hold' },
  { code: '002241', name: '歌尔股份', board: 'zz', price: 24.86, chg: 1.6, amount: 10.2, pe: 33.6, pb: 3.2, roe: 8.9, score: 55.2, rank: 26, signal: 'hold' },
  { code: '601668', name: '中国建筑', board: 'hs', price: 5.62, chg: 0.5, amount: 6.8, pe: 5.1, pb: 0.7, roe: 11.3, score: 55.4, rank: 27, signal: 'hold' },
  { code: '600887', name: '伊利股份', board: 'hs', price: 27.86, chg: 0.3, amount: 8.2, pe: 17.4, pb: 3.6, roe: 19.3, score: 56.2, rank: 28, signal: 'hold' },
  { code: '600941', name: '中国移动', board: 'hs', price: 108.2, chg: 0.3, amount: 5.4, pe: 12.8, pb: 1.6, roe: 10.8, score: 53.2, rank: 30, signal: 'hold' },
  { code: '002475', name: '立讯精密', board: 'hs', price: 38.42, chg: 0.7, amount: 12.6, pe: 22.4, pb: 3.5, roe: 20.8, score: 54.2, rank: 31, signal: 'hold' },
  { code: '600809', name: '山西汾酒', board: 'hs', price: 178.3, chg: 0.9, amount: 5.8, pe: 19.6, pb: 5.8, roe: 32.4, score: 53.6, rank: 32, signal: 'hold' },
  { code: '000858', name: '五粮液', board: 'hs', price: 128.4, chg: -0.8, amount: 9.6, pe: 15.2, pb: 3.8, roe: 24.1, score: 52.8, rank: 33, signal: 'hold' },
  { code: '600309', name: '万华化学', board: 'hs', price: 76.15, chg: 1.1, amount: 8.9, pe: 16.4, pb: 2.6, roe: 17.2, score: 52.1, rank: 36, signal: 'hold' },
  { code: '002050', name: '三花智控', board: 'zz', price: 28.54, chg: 0.8, amount: 6.8, pe: 31.2, pb: 5.4, roe: 15.2, score: 50.4, rank: 35, signal: 'hold' },
  { code: '002230', name: '科大讯飞', board: 'zz', price: 48.72, chg: 3.1, amount: 18.9, pe: 88.5, pb: 4.9, roe: 7.6, score: 68.4, rank: 10, signal: 'hold' },
  { code: '600460', name: '士兰微', board: 'zz', price: 27.35, chg: 2.9, amount: 12.4, pe: 65.3, pb: 5.1, roe: 5.8, score: 66.2, rank: 12, signal: 'hold' },
  { code: '603986', name: '兆易创新', board: 'zz', price: 118.4, chg: 2.4, amount: 9.8, pe: 45.2, pb: 6.3, roe: 8.1, score: 63.8, rank: 16, signal: 'hold' },
  { code: '002371', name: '北方华创', board: 'zz', price: 312.5, chg: 1.8, amount: 8.6, pe: 52.4, pb: 7.8, roe: 12.4, score: 62.5, rank: 17, signal: 'hold' },
  { code: '603501', name: '韦尔股份', board: 'zz', price: 108.6, chg: 2.2, amount: 11.8, pe: 38.4, pb: 4.6, roe: 10.2, score: 61.2, rank: 19, signal: 'hold' },
  { code: '600893', name: '航发动力', board: 'zz', price: 38.92, chg: 1.5, amount: 7.4, pe: 68.2, pb: 4.2, roe: 5.4, score: 57.6, rank: 24, signal: 'hold' },
  { code: '601633', name: '长城汽车', board: 'zz', price: 24.68, chg: -1.1, amount: 9.2, pe: 21.4, pb: 2.2, roe: 16.8, score: 46.3, rank: 40, signal: 'hold' },
  { code: '600150', name: '中国船舶', board: 'hs', price: 32.84, chg: 1.3, amount: 8.6, pe: 28.6, pb: 2.4, roe: 8.4, score: 54.8, rank: 29, signal: 'hold' },
  { code: '601989', name: '中国重工', board: 'zz', price: 6.18, chg: 0.9, amount: 5.2, pe: 45.8, pb: 1.8, roe: 4.2, score: 45.6, rank: 44, signal: 'hold' },
  { code: '002179', name: '中航光电', board: 'zz', price: 72.48, chg: 1.4, amount: 5.4, pe: 28.4, pb: 5.2, roe: 16.2, score: 58.4, rank: 24, signal: 'hold' },
  { code: '600760', name: '中航沈飞', board: 'zz', price: 48.62, chg: 1.1, amount: 4.6, pe: 52.4, pb: 4.8, roe: 9.4, score: 53.4, rank: 38, signal: 'hold' },
  { code: '601012', name: '隆基绿能', board: 'hs', price: 18.42, chg: -2.6, amount: 29.1, pe: 35.2, pb: 4.8, roe: 6.1, score: 41.3, rank: 46, signal: 'sell' },
  { code: '300750', name: '宁德时代', board: 'hs', price: 236.8, chg: -1.9, amount: 41.3, pe: 24.1, pb: 5.2, roe: 22.8, score: 39.8, rank: 52, signal: 'sell' },
  { code: '002594', name: '比亚迪', board: 'hs', price: 268.3, chg: -1.2, amount: 22.7, pe: 28.6, pb: 6.1, roe: 23.5, score: 37.2, rank: 58, signal: 'sell' },
  { code: '600745', name: '闻泰科技', board: 'zz', price: 32.16, chg: -1.6, amount: 8.4, pe: 42.8, pb: 2.9, roe: 6.2, score: 42.1, rank: 48, signal: 'sell' },
  { code: '603259', name: '药明康德', board: 'hs', price: 62.84, chg: -2.1, amount: 14.6, pe: 28.4, pb: 3.2, roe: 14.1, score: 40.8, rank: 54, signal: 'sell' },
  { code: '002714', name: '牧原股份', board: 'hs', price: 42.36, chg: -1.4, amount: 10.8, pe: 18.9, pb: 3.6, roe: 15.4, score: 39.2, rank: 57, signal: 'sell' },
  { code: '600585', name: '海螺水泥', board: 'hs', price: 24.16, chg: -0.7, amount: 5.8, pe: 18.6, pb: 0.8, roe: 4.2, score: 44.7, rank: 43, signal: 'hold' },
  { code: '000568', name: '泸州老窖', board: 'hs', price: 122.6, chg: 0.6, amount: 6.3, pe: 14.1, pb: 3.5, roe: 30.2, score: 51.2, rank: 34, signal: 'hold' },
  { code: '600765', name: '航发控制', board: 'zz', price: 21.38, chg: 0.8, amount: 3.2, pe: 38.6, pb: 3.8, roe: 7.2, score: 49.2, rank: 42, signal: 'hold' },
  { code: '603979', name: '金诚信', board: 'zz', price: 62.34, chg: 2.5, amount: 4.8, pe: 26.4, pb: 3.1, roe: 11.6, score: 59.1, rank: 22, signal: 'hold' },
  { code: '603993', name: '洛阳钼业', board: 'hs', price: 8.92, chg: 3.4, amount: 16.4, pe: 24.6, pb: 2.8, roe: 14.8, score: 69.5, rank: 10, signal: 'hold' },
  { code: '000933', name: '神火股份', board: 'zz', price: 18.26, chg: 3.0, amount: 9.2, pe: 12.8, pb: 1.9, roe: 18.4, score: 65.3, rank: 13, signal: 'hold' },
  { code: '600362', name: '江西铜业', board: 'hs', price: 23.54, chg: 2.6, amount: 11.6, pe: 16.4, pb: 1.4, roe: 10.8, score: 64.2, rank: 14, signal: 'hold' },
]

export interface SigRow {
  action: SignalType
  rank: number
  code: string
  name: string
  score: number
  trend: number
  value: number
  quality: number
  risk: number
  pe: number
  chg20: number
}

/** 信号明细 28 条（原型 sigs 数据迁移） */
export const sigs: SigRow[] = [
  { action: 'buy', rank: 4, code: '601899', name: '紫金矿业', score: 78.6, trend: 35, value: 19, quality: 15, risk: 9, pe: 18.2, chg20: 12.4 },
  { action: 'buy', rank: 7, code: '000333', name: '美的集团', score: 76.2, trend: 31, value: 20, quality: 17, risk: 8, pe: 11.6, chg20: 5.1 },
  { action: 'buy', rank: 9, code: '600886', name: '国投电力', score: 75.1, trend: 30, value: 21, quality: 16, risk: 8, pe: 14.3, chg20: 3.8 },
  { action: 'buy', rank: 11, code: '000792', name: '盐湖股份', score: 74.0, trend: 32, value: 17, quality: 16, risk: 9, pe: 22.7, chg20: 15.6 },
  { action: 'buy', rank: 13, code: '601088', name: '中国神华', score: 73.4, trend: 29, value: 22, quality: 15, risk: 7, pe: 9.8, chg20: 4.2 },
  { action: 'buy', rank: 14, code: '600900', name: '长江电力', score: 72.8, trend: 28, value: 22, quality: 16, risk: 7, pe: 19.5, chg20: 2.1 },
  { action: 'buy', rank: 15, code: '601318', name: '中国平安', score: 72.1, trend: 27, value: 21, quality: 15, risk: 9, pe: 7.9, chg20: 6.8 },
  { action: 'buy', rank: 15, code: '600028', name: '中国石化', score: 71.9, trend: 27, value: 22, quality: 14, risk: 9, pe: 8.7, chg20: 3.5 },
  { action: 'sell', rank: 46, code: '601012', name: '隆基绿能', score: 41.3, trend: 10, value: 12, quality: 11, risk: 8, pe: 35.2, chg20: -9.4 },
  { action: 'sell', rank: 52, code: '300750', name: '宁德时代', score: 39.8, trend: 9, value: 10, quality: 13, risk: 8, pe: 24.1, chg20: -6.2 },
  { action: 'sell', rank: 58, code: '002594', name: '比亚迪', score: 37.2, trend: 8, value: 9, quality: 12, risk: 8, pe: 28.6, chg20: -4.8 },
  { action: 'hold', rank: 22, code: '600036', name: '招商银行', score: 58.9, trend: 21, value: 18, quality: 12, risk: 8, pe: 6.2, chg20: 1.2 },
  { action: 'hold', rank: 27, code: '601668', name: '中国建筑', score: 55.4, trend: 20, value: 17, quality: 11, risk: 8, pe: 5.1, chg20: 0.8 },
  { action: 'hold', rank: 30, code: '600941', name: '中国移动', score: 53.2, trend: 19, value: 18, quality: 10, risk: 7, pe: 12.8, chg20: 1.9 },
  { action: 'hold', rank: 18, code: '600519', name: '贵州茅台', score: 61.5, trend: 21, value: 20, quality: 24, risk: 8, pe: 21.5, chg20: 3.1 },
  { action: 'hold', rank: 20, code: '000651', name: '格力电器', score: 60.3, trend: 19, value: 22, quality: 24, risk: 8, pe: 8.4, chg20: 1.8 },
  { action: 'hold', rank: 21, code: '600690', name: '海尔智家', score: 59.6, trend: 20, value: 21, quality: 17, risk: 8, pe: 13.2, chg20: 2.4 },
  { action: 'hold', rank: 23, code: '601728', name: '中国电信', score: 57.9, trend: 20, value: 21, quality: 9, risk: 7, pe: 16.8, chg20: 1.5 },
  { action: 'hold', rank: 24, code: '000858', name: '五粮液', score: 52.8, trend: 15, value: 21, quality: 26, risk: 7, pe: 15.2, chg20: -0.6 },
  { action: 'hold', rank: 25, code: '601100', name: '恒立液压', score: 56.8, trend: 24, value: 17, quality: 19, risk: 7, pe: 24.6, chg20: 4.9 },
  { action: 'hold', rank: 26, code: '002241', name: '歌尔股份', score: 55.2, trend: 26, value: 15, quality: 9, risk: 7, pe: 33.6, chg20: 5.7 },
  { action: 'hold', rank: 28, code: '600887', name: '伊利股份', score: 56.2, trend: 21, value: 21, quality: 19, risk: 7, pe: 17.4, chg20: 1.1 },
  { action: 'hold', rank: 31, code: '002475', name: '立讯精密', score: 54.2, trend: 22, value: 20, quality: 21, risk: 7, pe: 22.4, chg20: 2.8 },
  { action: 'hold', rank: 33, code: '600309', name: '万华化学', score: 52.1, trend: 18, value: 22, quality: 17, risk: 7, pe: 16.4, chg20: 3.3 },
  { action: 'hold', rank: 35, code: '603501', name: '韦尔股份', score: 61.2, trend: 27, value: 14, quality: 10, risk: 7, pe: 38.4, chg20: 6.2 },
  { action: 'sell', rank: 61, code: '603259', name: '药明康德', score: 38.4, trend: 12, value: 11, quality: 14, risk: 7, pe: 28.4, chg20: -7.1 },
]

/** 每日 pipeline 步骤 */
export const steps: Array<[string, string, string]> = [
  ['16:30', '行情同步', '完成'],
  ['16:45', '估值同步', '完成'],
  ['18:30', '数据健康', '完成'],
  ['19:00', '因子计算', '完成'],
  ['19:30', '信号生成', '完成'],
  ['19:35', '模拟交易', '完成'],
  ['21:15', '净值对账', '完成'],
]

/** 数据健康检查项 */
export const healthChecks: Array<{ name: string; val: string; pct: number; ok: boolean }> = [
  { name: '行情覆盖率', val: '99.8%', pct: 0.998, ok: true },
  { name: '估值覆盖率', val: '99.2%', pct: 0.992, ok: true },
  { name: '财务新鲜度', val: '20季度', pct: 0.95, ok: true },
  { name: '价格异常检测', val: '0 条', pct: 1, ok: true },
  { name: '重复数据检查', val: '0 行', pct: 1, ok: true },
  { name: '缺失交易日', val: '0 日', pct: 1, ok: true },
  { name: '指数基准完整', val: '正常', pct: 1, ok: true },
]

/** 运维：服务状态 */
export const services: Array<[string, string, string]> = [
  ['collector', '数据采集', '运行中 · 18h'],
  ['quant-engine', '因子引擎', '运行中 · 18h'],
  ['backend', '交易后端', '运行中 · 32d'],
  ['frontend', '前端', '运行中 · 32d'],
  ['postgres', '数据库', '运行中 · 32d'],
  ['nginx', '网关', '运行中 · 32d'],
]

/** 权重实验台因子定义 */
export const weightDefs: Array<[string, string, number]> = [
  ['ma_trend', '均线趋势 (ma_trend)', 20],
  ['macd_signal', 'MACD信号 (macd_signal)', 20],
  ['pe_ratio', '市盈率 (pe_ratio)', 15],
  ['pb_ratio', '市净率 (pb_ratio)', 15],
  ['roe_quality', '盈利质量 (roe_quality)', 20],
  ['debt_risk', '负债风险 (debt_risk)', 10],
]

/** 页面面包屑：pageKey → [标题, 副标题] */
export const crumbs: Record<string, [string, string]> = {
  dash: ['总览', '账户与策略运行全景'],
  factor: ['因子实验室', '因子有效性诊断与配权实验'],
  facgen: ['因子工厂', '新建因子 · 编辑与版本管理 · 状态流转'],
  signal: ['策略与信号', '多因子轮动 (multi_factor) 每日信号明细'],
  strgen: ['策略工厂', '策略构建 · 编辑与状态管理 · A/B 对比'],
  brief: ['早盘简报', '隔夜外盘 · 热点板块 · 今日计划 · AI 解读'],
  stocks: ['股票池', '沪深300 + 中证500 · 搜索与筛选'],
  stockd: ['个股详情', 'K线 · 因子得分 · 财务指标 · 信号历史'],
  trade: ['模拟交易', '账户 · 持仓 · 委托 · 成交'],
  backtest: ['回测中心', '策略历史检验与偏差校准'],
  live: ['实盘交易', 'Phase 3 概念设计 · 未实现'],
  auth: ['登录与安全', 'Phase 3 概念设计 · 访问控制与审计'],
  ops: ['运维监控', '任务调度 · 数据健康 · 告警'],
  set: ['设置', '数据源 · 通知 · AI 助手'],
}
