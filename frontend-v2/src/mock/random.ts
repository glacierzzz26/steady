/**
 * 种子随机数与序列生成器 —— 移植自原型内联 JS，
 * 保证每次渲染的 mock 曲线稳定一致。
 */

/** 线性同余随机序列 */
export function lcg(seed: number): () => number {
  let s = seed % 2147483647
  if (s <= 0) s += 2147483646
  return () => {
    s = (s * 16807) % 2147483647
    return s / 2147483647
  }
}

/** 字符串哈希 → 种子 */
export function hash(str: string): number {
  let h = 7
  for (const ch of str) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return h || 7
}

/** 全局共享随机流（等价原型 seed=42） */
const rnd = lcg(42)
export const g = (a: number, b: number) => a + (b - a) * rnd()

/** 近 n 个交易日日期标签（M/D），以 2026-08-21 为锚 */
export function dates(n: number): string[] {
  const out: string[] = []
  const d = new Date(2026, 7, 21)
  for (let i = n - 1; i >= 0; i--) {
    const x = new Date(d)
    x.setDate(d.getDate() - i)
    if (x.getDay() !== 0 && x.getDay() !== 6) out.push(`${x.getMonth() + 1}/${x.getDate()}`)
  }
  return out.slice(0, n)
}

/** 2019-01 ~ 2026-08 月度标签 */
export const months: string[] = (() => {
  const out: string[] = []
  for (let y = 2019; y <= 2026; y++) {
    for (let m = 1; m <= 12; m++) {
      if (y === 2026 && m > 8) break
      out.push(y + '-' + String(m).padStart(2, '0'))
    }
  }
  return out
})()

/** 2019Q1 ~ 2026Q3 季度标签 */
export const yrs: string[] = (() => {
  const out: string[] = []
  for (let y = 2019; y <= 2026; y++) {
    for (let q = 1; q <= 4; q++) {
      if (y === 2026 && q > 3) break
      out.push(y + 'Q' + q)
    }
  }
  return out
})()

/** 净值随机游走 */
export function navSeries(n: number, base: number, vol: number, drift: number): number[] {
  const v = [base]
  for (let i = 1; i < n; i++) v.push(v[i - 1] * (1 + drift + g(-vol, vol)))
  return v
}

/** 分层回测净值（年化 ann） */
export function layerSeries(n: number, ann: number): number[] {
  let v = 1
  const out: number[] = []
  for (let i = 0; i < n; i++) {
    v *= 1 + ann / 252 + g(-0.014, 0.014)
    out.push(+v.toFixed(3))
  }
  return out
}

/** 回测净值（含中段回撤形态） */
export function btSeries(n: number, ann: number, vol: number, dd: boolean): number[] {
  let v = 1
  let peak = 1
  const out: number[] = []
  for (let i = 0; i < n; i++) {
    v *= 1 + ann / 4 + g(-vol, vol)
    peak = Math.max(peak, v)
    if (dd && i > Math.floor(n * 0.6) && i < Math.floor(n * 0.75)) v *= 0.96
    out.push(+v.toFixed(3))
  }
  return out
}

export interface KlineStock {
  code: string
  price: number
}

/** 按股票代码生成 120 根日 K（收在真实价格附近） */
export function genKline(s: KlineStock): { d: number[][]; v: number[] } {
  const r = lcg(hash(s.code))
  const rg = (a: number, b: number) => a + (b - a) * r()
  let p = s.price * 0.82
  const d: number[][] = []
  const v: number[] = []
  for (let i = 0; i < 120; i++) {
    const o = p
    const c = +(o * (1 + rg(-0.028, 0.034))).toFixed(2)
    const l = +Math.min(o, c, c * (1 - rg(0, 0.018))).toFixed(2)
    const h = +Math.max(o, c, c * (1 + rg(0, 0.018))).toFixed(2)
    p = c
    d.push([o, c, l, h])
    v.push(Math.round(rg(800, 62000)))
  }
  const sc = s.price / d[119][1]
  for (let i = 0; i < 120; i++) {
    d[i] = [
      +(d[i][0] * sc).toFixed(2),
      +(d[i][1] * sc).toFixed(2),
      +(d[i][2] * sc).toFixed(2),
      +(d[i][3] * sc).toFixed(2),
    ]
  }
  return { d, v }
}

/** K 线 MA 均线 */
export const maCalc = (d: number[][], n: number): (number | null)[] =>
  d.map((_, i) => {
    if (i < n - 1) return null
    let s = 0
    for (let j = i - n + 1; j <= i; j++) s += d[j][1]
    return +(s / n).toFixed(2)
  })
