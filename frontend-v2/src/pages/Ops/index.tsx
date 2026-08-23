import Notice from '../../components/Notice'
import { opsApi } from '../../api'
import { useApi } from '../../hooks/useApi'

const G7_HINT = '待 G7 后端接口'

const STATUS_TEXT: Record<string, [string, string]> = {
  success: ['成功', 'ok'],
  skipped: ['已跳过', 'ok'],
  failed: ['失败', 'fail'],
}

function HealthRow({ label, state }: { label: string; state: 'ok' | 'down' | 'g7' }) {
  const cfg =
    state === 'ok'
      ? ['运行中', '100%', 'var(--ok)']
      : state === 'down'
        ? ['异常', '40%', 'var(--down)']
        : ['待 G7', '15%', 'rgba(139,147,167,.35)']
  return (
    <div className="health" title={state === 'g7' ? G7_HINT : undefined}>
      <span style={{ width: 110 }}>
        <b style={{ fontWeight: 500 }}>{label}</b>
      </span>
      <div className="hbar">
        <i style={{ width: cfg[1], background: cfg[2] }} />
      </div>
      <span
        className="num"
        style={{
          width: 92,
          textAlign: 'right',
          fontSize: 13,
          color: state === 'g7' ? 'var(--txt3)' : 'var(--txt2)',
        }}
      >
        {cfg[0]}
      </span>
    </div>
  )
}

export default function Ops() {
  const runs = useApi(() => opsApi.getTaskRuns(30), [])
  const health = useApi(() => opsApi.getHealth(), [])

  const items = runs.data?.items ?? []
  const backendOk = health.data?.status === 'ok'
  const dbOk = health.data?.db === 'ok'

  return (
    <section className="page">
      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr 1fr', marginBottom: 14 }}>
        <div className="card">
          <h3>
            任务执行时间线
            <span className="hint">
              {health.data?.time ? `${health.data.time.slice(0, 16).replace('T', ' ')} 实时` : '近 30 次'}
            </span>
          </h3>
          {runs.error ? (
            <Notice text={runs.error} onRetry={runs.reload} retrying={runs.loading} />
          ) : runs.loading && !runs.data ? (
            <div className="empty">任务记录加载中…</div>
          ) : items.length === 0 ? (
            <div className="empty">暂无任务记录</div>
          ) : (
            <div className="tl">
              {items.map(item => {
                const [statusText, cls] = STATUS_TEXT[item.status] ?? [item.status, '']
                const tm = item.created_at || item.run_date
                return (
                  <div className={`tl-item ${cls}`} key={item.id}>
                    <div className="t">
                      {tm.slice(5, 16)} · {statusText}
                    </div>
                    <div className="n">{item.task_name}</div>
                    {item.message && (
                      <div style={{ fontSize: 13.5, color: 'var(--txt3)' }}>{item.message}</div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        <div className="card">
          <h3>
            服务状态<span className="hint">/health 直连 · 其余待 G7</span>
          </h3>
          {health.error ? (
            <Notice text={health.error} onRetry={health.reload} retrying={health.loading} />
          ) : health.loading && !health.data ? (
            <div className="empty">健康检查加载中…</div>
          ) : (
            <>
              <HealthRow label="backend 交易后端" state={backendOk ? 'ok' : 'down'} />
              <HealthRow label="postgres 数据库" state={dbOk ? 'ok' : 'down'} />
              <HealthRow label="collector 数据采集" state="g7" />
              <HealthRow label="quant-engine 因子引擎" state="g7" />
              <HealthRow label="frontend 前端" state="g7" />
              <HealthRow label="nginx 网关" state="g7" />
              <div
                style={{
                  marginTop: 10,
                  fontSize: 13,
                  color: 'var(--txt3)',
                  borderTop: '1px solid var(--line)',
                  paddingTop: 10,
                }}
              >
                backend / db 两行为 /health 实时结果；collector / engine 等服务探活接口{G7_HINT}。
              </div>
            </>
          )}
        </div>

        <div className="card">
          <h3>
            告警记录<span className="hint">近 7 日 · {G7_HINT}</span>
          </h3>
          <div className="empty" style={{ padding: '56px 0' }}>
            告警记录接口{G7_HINT}（「该做没做」监控规则已固化，见监控告警文档）
          </div>
        </div>
      </div>

      <div className="card">
        <h3>
          数据资产概览<span className="hint">数据即地基 · {G7_HINT}</span>
        </h3>
        <div className="empty" style={{ padding: '36px 0' }}>
          数据资产行数统计接口{G7_HINT}（DB COUNT 已可实现，零费用）
        </div>
      </div>
    </section>
  )
}
