/** 绩效度量页 /performance —— 方向① 第一期
 *
 * 数据源：quant-engine 每日 21:20 预计算的 strategy_perf（只读）：
 *  - 命中率：BUY 信号 forward 5/10/20 交易日收益（后复权），hit_rate + 相对基准命中
 *  - 对照：实盘 account_nav vs 回测 t1_open nav vs 基准 sh000300 归一叠加
 * 样本不足时如实显示「待积累」（hit_rate/samples 为 null/0）。
 */
import { useMemo } from 'react'
import EChart from '../../components/EChart'
import Kpi from '../../components/Kpi'
import Notice from '../../components/Notice'
import { useApi } from '../../hooks/useApi'
import { performanceApi } from '../../api'
import { fmtRatioPct } from '../../lib/format'
import { lineOpt } from '../../mock/chartOpt'

const WINDOWS = [
  { key: '5', label: '5 日' },
  { key: '10', label: '10 日' },
  { key: '20', label: '20 日' },
]

/** 命中率小数比例 → 带色文字（涨红跌绿，沪深习惯） */
function rateClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return ''
  return v >= 0.5 ? 'up' : 'down'
}

export default function Performance() {
  const hr = useApi(() => performanceApi.getHitRate(), [])
  const ov = useApi(() => performanceApi.getNavOverlay(), [])

  const windows = hr.data?.detail?.windows ?? {}
  const hitPoints = hr.data?.detail?.buy_samples ?? 0
  const sellPoints = hr.data?.detail?.sell_samples ?? 0
  const w5 = windows['5']

  /** 对照叠加图：live / bt / benchmark 三线归一净值 */
  const overlayOption = useMemo(() => {
    const pts = ov.data?.series ?? []
    return lineOpt(
      {
        dates: pts.map(p => p.date),
        series: [
          { name: '实盘', data: pts.map(p => p.live), w: 2 },
          { name: '回测', data: pts.map(p => p.bt) },
          { name: '基准(沪深300)', data: pts.map(p => p.benchmark) },
        ],
      },
      ['#4C7DFF', '#2FBF71', '#8B93A7'],
      true,
    )
  }, [ov.data])

  const m = ov.data?.metrics

  return (
    <div className="page">
      {/* ================= 信号命中率 ================= */}
      <section className="card">
        <div className="hd">
          <h2>信号命中率</h2>
          <span className="muted">
            样本 {hitPoints} BUY / {sellPoints} SELL · 窗口为 forward 交易日（后复权收益）· 每日 21:20 重算
          </span>
        </div>

        {hr.error && <Notice text={hr.error} onRetry={hr.reload} retrying={hr.loading} />}
        {!hr.error && !hr.data && (
          <div className="empty">暂无命中率数据 —— 每日 21:20 预计算后可见（今日发布首日次日生成）</div>
        )}
        {!hr.error && hr.data && (
          <>
            <div className="grid kpi-grid">
              {WINDOWS.map(w => {
                const p = windows[w.key]
                const samples = p?.samples ?? 0
                return (
                  <Kpi
                    key={w.key}
                    lb={`BUY 命中率 · ${w.label}`}
                    v={samples ? fmtRatioPct(p?.hit_rate, 0) : '待积累'}
                    vClass={samples ? rateClass(p?.hit_rate) : undefined}
                    d={samples ? `${samples} 样本` : '样本不足'}
                  />
                )
              })}
              <Kpi
                lb="相对基准 · 5日"
                v={w5?.samples ? fmtRatioPct(w5.relative_hit, 0) : '待积累'}
                vClass={w5?.samples ? rateClass(w5.relative_hit) : undefined}
                d={w5?.samples ? `跑赢沪深300 ${fmtRatioPct(w5.relative_hit, 0)}` : '样本不足'}
              />
              <Kpi
                lb="实盘累计收益"
                v={m?.live_cum_return !== null && m?.live_cum_return !== undefined ? fmtRatioPct(m.live_cum_return) : '--'}
                vClass={m?.live_cum_return != null ? rateClass(m.live_cum_return) : undefined}
                d="自首日净值"
              />
              <Kpi
                lb="实盘 vs 回测 drift"
                v={m?.drift !== null && m?.drift !== undefined ? fmtRatioPct(m.drift) : '--'}
                vClass={m?.drift != null ? rateClass(m.drift) : undefined}
                d={m?.bt_points ? `回测 ${fmtRatioPct(m.bt_cum_return)}` : '回测待积累'}
              />
            </div>

            <div className="muted hint">说明：命中率 = BUY 信号 forward 窗口收益大于 0 的占比；相对基准 = 跑赢同期沪深300 的占比。同股票 30 天内重复 BUY 已去重。</div>

            <table>
              <thead>
                <tr>
                  <th>窗口</th>
                  <th className="r">样本数</th>
                  <th className="r">hit_rate</th>
                  <th className="r">相对基准</th>
                  <th className="r">平均收益</th>
                  <th className="r">中位数</th>
                  <th className="r">超额均值</th>
                  <th className="r">SELL 后下跌率</th>
                </tr>
              </thead>
              <tbody>
                {WINDOWS.map(w => {
                  const p = windows[w.key]
                  if (!p) {
                    return (
                      <tr key={w.key}>
                        <td>{w.label}</td>
                        <td className="r">0</td>
                        <td className="r" colSpan={6}>待积累</td>
                      </tr>
                    )
                  }
                  return (
                    <tr key={w.key}>
                      <td>{w.label}</td>
                      <td className="r">{p.samples}</td>
                      <td className={`r ${rateClass(p.hit_rate)}`}>{p.samples ? fmtRatioPct(p.hit_rate) : '--'}</td>
                      <td className={`r ${rateClass(p.relative_hit)}`}>{p.samples ? fmtRatioPct(p.relative_hit) : '--'}</td>
                      <td className={`r ${rateClass(p.avg)}`}>{p.samples ? fmtRatioPct(p.avg) : '--'}</td>
                      <td className={`r ${rateClass(p.median)}`}>{p.samples ? fmtRatioPct(p.median) : '--'}</td>
                      <td className={`r ${rateClass(p.avg_excess)}`}>{p.samples ? fmtRatioPct(p.avg_excess) : '--'}</td>
                      <td className="r">{p.sell_samples ? fmtRatioPct(p.sell_hit_rate) : '--'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </>
        )}
      </section>

      {/* ================= 实盘 vs 回测 vs 基准 ================= */}
      <section className="card">
        <div className="hd">
          <h2>实盘 vs 回测 vs 基准</h2>
          <span className="muted">
            {ov.data
              ? `${ov.data.period_start} ~ ${ov.data.period_end} · 各以起点归一 1.0`
              : '起点归一净值叠加'}
          </span>
        </div>
        {ov.error && <Notice text={ov.error} onRetry={ov.reload} retrying={ov.loading} />}
        {!ov.error && !ov.data && <div className="empty">暂无对照数据 —— 每日 21:20 预计算后可见</div>}
        {!ov.error && ov.data && (
          <>
            <EChart option={overlayOption} height={320} />
            <table>
              <thead>
                <tr>
                  <th>指标</th>
                  <th className="r">实盘</th>
                  <th className="r">回测(t1_open)</th>
                  <th className="r">drift(实盘−回测)</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>累计收益</td>
                  <td className={`r ${rateClass(m?.live_cum_return)}`}>{fmtRatioPct(m?.live_cum_return)}</td>
                  <td className={`r ${rateClass(m?.bt_cum_return)}`}>{fmtRatioPct(m?.bt_cum_return)}</td>
                  <td className={`r ${rateClass(m?.drift)}`}>{fmtRatioPct(m?.drift)}</td>
                </tr>
                <tr>
                  <td>最大回撤</td>
                  <td className="r">{fmtRatioPct(m?.live_max_drawdown)}</td>
                  <td className="r">{fmtRatioPct(m?.bt_max_drawdown)}</td>
                  <td className="r">--</td>
                </tr>
                <tr>
                  <td>点数</td>
                  <td className="r">{m?.live_points ?? 0}</td>
                  <td className="r">{m?.bt_points ?? 0}</td>
                  <td className="r">--</td>
                </tr>
              </tbody>
            </table>
          </>
        )}
      </section>
    </div>
  )
}
