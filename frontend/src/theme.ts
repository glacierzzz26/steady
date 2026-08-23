/**
 * 设计 tokens —— 与 index.css 中的 CSS 变量保持一致，
 * 供 ECharts 配置等 JS 侧使用（参考 Steady/frontend/src/theme.ts 的导出方式）。
 */
export const tokens = {
  bg: '#0B0E14',
  panel: '#12161F',
  panel2: '#171C27',
  line: 'rgba(255,255,255,.07)',
  line2: 'rgba(255,255,255,.12)',
  txt: '#E6EAF2',
  txt2: '#8B93A7',
  txt3: '#5C6478',
  brand: '#4C7DFF',
  brand2: '#6C5CE7',
  up: '#F0524F',
  down: '#2FBF71',
  warn: '#E9A23B',
  ok: '#2FBF71',
  brandSoft: '#A8C0FF',
  plan: '#A99CF0',
  /** 深色 tooltip 统一配置 */
  tooltip: {
    backgroundColor: '#171C27',
    borderColor: 'rgba(255,255,255,.12)',
    textStyle: { color: '#E6EAF2', fontSize: 14 },
  },
} as const
