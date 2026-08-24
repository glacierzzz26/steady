/**
 * ECharts option 工厂 —— 移植自原型内联 JS，
 * 深色终端风统一样式。
 */
import type { EChartsOption } from 'echarts'
import { tokens } from '../theme'
import { g, maCalc } from './random'

export const axis = {
  axisLine: { lineStyle: { color: 'rgba(255,255,255,.15)' } },
  axisLabel: { color: '#8B93A7', fontSize: 13 },
  splitLine: { lineStyle: { color: 'rgba(255,255,255,.05)' } },
  axisTick: { show: false },
}

const tip = tokens.tooltip

export interface LineSeriesDef {
  name: string
  data: (number | null)[] // null = 断点（对齐两个日期序列时用）
  w?: number
}

/** 通用折线图（净值 / 分层等） */
export function lineOpt(
  ds: { dates: string[]; series: LineSeriesDef[] },
  colors: string[],
  area: boolean,
): EChartsOption {
  return {
    grid: { left: 52, right: 16, top: 26, bottom: 28 },
    tooltip: { trigger: 'axis', ...tip },
    legend: {
      show: ds.series.length > 1,
      textStyle: { color: '#8B93A7', fontSize: 13 },
      top: 0,
    },
    xAxis: { type: 'category', data: ds.dates, ...axis },
    yAxis: {
      type: 'value',
      scale: true,
      ...axis,
      axisLabel: {
        ...axis.axisLabel,
        formatter: (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)),
      },
    },
    series: ds.series.map((s, i) => ({
      name: s.name,
      type: 'line',
      data: s.data,
      smooth: 0.3,
      symbol: 'none',
      lineStyle: { width: s.w || 1.6, color: colors[i] },
      itemStyle: { color: colors[i] },
      areaStyle:
        area && i === 0
          ? {
              color: {
                type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: 'rgba(76,125,255,.22)' },
                  { offset: 1, color: 'rgba(76,125,255,0)' },
                ],
              },
            }
          : null,
    })),
  } as EChartsOption
}

/** RankIC 月度柱状 + 累计线 */
export function icBarOpt(months: string[], icBars: number[], cumIc: number[]): EChartsOption {
  return {
    grid: { left: 44, right: 48, top: 22, bottom: 26 },
    tooltip: { trigger: 'axis', ...tip },
    xAxis: { type: 'category', data: months, ...axis, axisLabel: { ...axis.axisLabel, interval: 11 } },
    yAxis: { type: 'value', ...axis },
    series: [
      {
        name: '月度RankIC',
        type: 'bar',
        data: icBars,
        itemStyle: {
          color: (p: { value: number }) => (p.value >= 0 ? 'rgba(240,82,79,.75)' : 'rgba(47,191,113,.75)'),
        },
      },
      {
        name: '累计IC',
        type: 'line',
        data: cumIc,
        symbol: 'none',
        lineStyle: { width: 1.6, color: '#E9A23B' },
        itemStyle: { color: '#E9A23B' },
      },
    ],
  } as EChartsOption
}

/** IC 衰减曲线 */
export function decayOpt(): EChartsOption {
  return {
    grid: { left: 44, right: 16, top: 26, bottom: 28 },
    tooltip: { trigger: 'axis', ...tip },
    legend: { textStyle: { color: '#8B93A7', fontSize: 13 }, top: 0, data: ['roe_quality', 'pe_ratio', 'ma_trend'] },
    xAxis: { type: 'category', data: ['1日', '5日', '10日', '20日', '40日', '60日'], ...axis },
    yAxis: { type: 'value', ...axis },
    series: [
      { name: 'roe_quality', type: 'line', data: [.052, .047, .041, .032, .021, .014], symbol: 'circle', symbolSize: 5, lineStyle: { width: 1.8, color: '#4C7DFF' }, itemStyle: { color: '#4C7DFF' } },
      { name: 'pe_ratio', type: 'line', data: [-.038, -.034, -.029, -.022, -.015, -.009], symbol: 'circle', symbolSize: 5, lineStyle: { width: 1.8, color: '#2FBF71' }, itemStyle: { color: '#2FBF71' } },
      { name: 'ma_trend', type: 'line', data: [.034, .025, .016, .009, .002, -.004], symbol: 'circle', symbolSize: 5, lineStyle: { width: 1.8, color: '#E9A23B' }, itemStyle: { color: '#E9A23B' } },
    ],
  }
}

/** 因子相关性矩阵（热力图） */
export function corrOpt(facs: string[], corr: number[][]): EChartsOption {
  return {
    grid: { left: 60, right: 16, top: 26, bottom: 52 },
    tooltip: {
      ...tip,
      formatter: (p: unknown) => {
        const d = (p as { value: [number, number, number] }).value
        return `${facs[d[1]]} × ${facs[d[0]]}：<b>${corr[d[1]][d[0]]}</b>`
      },
    },
    xAxis: { type: 'category', data: facs, ...axis },
    yAxis: { type: 'category', data: facs, ...axis },
    visualMap: {
      min: -1, max: 1, calculable: false, orient: 'horizontal',
      left: 'center', bottom: 0,
      textStyle: { color: '#8B93A7', fontSize: 13 },
      inRange: { color: ['#2FBF71', '#12161F', '#F0524F'] },
      show: false,
    },
    series: [{
      type: 'heatmap',
      data: ([] as number[][]).concat(...corr.map((r, i) => r.map((v, j) => [j, i, v]))),
      label: {
        show: true, color: '#E6EAF2', fontSize: 14,
        formatter: (p: { value: number[] }) => p.value[2].toFixed(2).replace('0.', '.').replace(/^-.?0/, '-0'),
      },
      itemStyle: { borderColor: '#0B0E14', borderWidth: 2 },
    }],
  } as EChartsOption
}

/** K 线 + 成交量 */
/** K线 tooltip 中文渲染：开盘/收盘/最低/最高（替代 ECharts 默认英文 open/close/lowest/highest） */
function klineTipFmt(ps: unknown): string {
  const params = (Array.isArray(ps) ? ps : [ps]) as Array<{
    axisValue?: string
    seriesName: string
    value: unknown
  }>
  const lines = [params[0]?.axisValue ?? '']
  for (const p of params) {
    if (p.seriesName === '日K' && Array.isArray(p.value) && p.value.length === 4) {
      const [open, close, low, high] = p.value as number[]
      const f = (x: number) => (x === null || x === undefined ? '--' : x.toFixed(2))
      lines.push(`开盘: ${f(open)}　收盘: ${f(close)}`)
      lines.push(`最低: ${f(low)}　最高: ${f(high)}`)
    } else if (typeof p.value === 'number') {
      const v = p.value
      lines.push(`${p.seriesName}: ${v >= 10000 ? `${(v / 10000).toFixed(2)}万` : v.toFixed(2)}`)
    }
  }
  return lines.join('<br/>')
}

export function klineOpt(kdates: string[], d: number[][], v: number[]): EChartsOption {
  const n = kdates.length
  // 默认显示最近 120 个交易日（全量十年可拖回）；主图与成交量轴联动
  const startIdx = Math.max(0, n - 120)
  const zoom: Record<string, unknown> = { xAxisIndex: [0, 1], startValue: startIdx, endValue: n - 1 }
  return {
    grid: [
      { left: 52, right: 16, top: 26, height: 170 },
      { left: 52, right: 16, top: 214, height: 36, bottom: 28 },
    ],
    dataZoom: [
      { type: 'inside', ...zoom },
      {
        type: 'slider',
        ...zoom,
        bottom: 4,
        height: 16,
        borderColor: 'rgba(255,255,255,.08)',
        backgroundColor: 'rgba(255,255,255,.03)',
        fillerColor: 'rgba(76,125,255,.16)',
        handleStyle: { color: '#4C7DFF', borderColor: '#4C7DFF' },
        textStyle: { color: '#8B93A7', fontSize: 11 },
        dataBackground: {
          lineStyle: { color: 'rgba(139,147,167,.4)' },
          areaStyle: { color: 'rgba(139,147,167,.12)' },
        },
      },
    ],
    tooltip: { trigger: 'axis', ...tip, axisPointer: { type: 'cross' }, formatter: klineTipFmt },
    legend: { textStyle: { color: '#8B93A7', fontSize: 13 }, top: 0, data: ['MA5', 'MA20'] },
    xAxis: [
      { type: 'category', data: kdates, ...axis, axisLabel: { show: false } },
      { type: 'category', data: kdates, gridIndex: 1, ...axis },
    ],
    yAxis: [
      { scale: true, ...axis },
      { gridIndex: 1, ...axis, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      { name: '日K', type: 'candlestick', data: d, itemStyle: { color: '#F0524F', color0: '#2FBF71', borderColor: '#F0524F', borderColor0: '#2FBF71' } },
      { name: 'MA5', type: 'line', data: maCalc(d, 5), symbol: 'none', lineStyle: { width: 1.2, color: '#E9A23B' }, itemStyle: { color: '#E9A23B' } },
      { name: 'MA20', type: 'line', data: maCalc(d, 20), symbol: 'none', lineStyle: { width: 1.2, color: '#4C7DFF' }, itemStyle: { color: '#4C7DFF' } },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: v, itemStyle: { color: 'rgba(139,147,167,.45)' } },
    ],
  } as EChartsOption
}

/** 因子得分雷达 */
export function radarOpt(vals: number[]): EChartsOption {
  return {
    radar: {
      indicator: [
        { name: '趋势', max: 100 },
        { name: '价值', max: 100 },
        { name: '质量', max: 100 },
        { name: '风险', max: 100 },
      ],
      radius: '66%',
      axisName: { color: '#8B93A7', fontSize: 13 },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,.08)' } },
      splitArea: { show: false },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,.1)' } },
    },
    series: [{
      type: 'radar',
      data: [{
        value: vals,
        name: '因子得分',
        symbolSize: 3,
        lineStyle: { color: '#4C7DFF', width: 1.8 },
        itemStyle: { color: '#4C7DFF' },
        areaStyle: { color: 'rgba(76,125,255,.22)' },
      }],
    }],
  }
}

/** 因子工厂：试算 RankIC 迷你柱图 */
export function ficBarOpt(months: string[], fic: number[]): EChartsOption {
  return {
    grid: { left: 40, right: 8, top: 8, bottom: 18 },
    tooltip: { trigger: 'axis', ...tip },
    xAxis: { type: 'category', data: months, ...axis, axisLabel: { show: false } },
    yAxis: { type: 'value', ...axis, axisLabel: { show: false }, splitLine: { show: false } },
    series: [{
      type: 'bar',
      data: fic,
      itemStyle: { color: (p: { value: number }) => (p.value >= 0 ? 'rgba(240,82,79,.8)' : 'rgba(47,191,113,.8)') },
    }],
  } as EChartsOption
}

/** 参数寻优热力图 */
export function hmapOpt(x: string[], y: string[], data: number[][]): EChartsOption {
  const vals = data.map(d => d[2])
  return {
    grid: { left: 64, right: 14, top: 26, bottom: 48 },
    tooltip: {
      ...tip,
      formatter: (p: unknown) => {
        const d = (p as { value: [number, number, number] }).value
        return `${y[d[1]]} × ${x[d[0]]}：<b>${d[2]}</b>`
      },
    },
    xAxis: { type: 'category', data: x, ...axis },
    yAxis: { type: 'category', data: y, ...axis },
    visualMap: {
      min: Math.min(...vals), max: Math.max(...vals),
      calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
      textStyle: { color: '#8B93A7', fontSize: 13 },
      inRange: { color: ['#1B2230', '#4C7DFF', '#A8C0FF'] },
      show: false,
    },
    series: [{
      type: 'heatmap',
      data,
      itemStyle: { borderColor: '#0B0E14', borderWidth: 2 },
      label: { show: true, color: '#E6EAF2', fontSize: 13, formatter: (p: { value: number[] }) => p.value[2].toFixed(3).replace(/^0/, '') },
    }],
  } as EChartsOption
}

/** 生成相关性矩阵 mock（种子随机，对角为 1） */
export function genCorr(facs: string[]): number[][] {
  return facs.map((f, i) =>
    facs.map((f2, j) => (i === j ? 1 : +(g(-0.4, 0.55).toFixed(2)))),
  )
}
