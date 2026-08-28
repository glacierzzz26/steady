import { useState } from 'react'
import Notice from '../../components/Notice'
import HealthChecks from '../../components/HealthChecks'
import { opsApi, type ServiceStatus } from '../../api'
import { useApi } from '../../hooks/useApi'
import { fmtTask } from '../../lib/names'

const STATUS_TEXT: Record<string, [string, string]> = {
  success: ['成功', 'ok'],
  skipped: ['已跳过', 'ok'],
  failed: ['失败', 'fail'],
}

/** 服务状态行（来自 /health/services 6 容器实时探活） */
function ServiceRow({ svc }: { svc: ServiceStatus }) {
  const cfg =
    svc.status === 'ok'
      ? ['运行中', '100%', 'var(--ok)']
      : svc.status === 'down'
        ? ['异常', '40%', 'var(--down)']
        : ['未知', '15%', 'rgba(139,147,167,.35)']
  return (
    <div className="health" title={svc.detail || undefined}>
      <span style={{ width: 110 }}>
        <b style={{ fontWeight: 500 }}>{svc.label}</b>
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
          color: svc.status === 'unknown' ? 'var(--txt3)' : 'var(--txt2)',
        }}
      >
        {cfg[0]}
      </span>
    </div>
  )
}

export default function Ops() {
  const runs = useApi(() => opsApi.getTaskRuns(100), [])
  const health = useApi(() => opsApi.getHealth(), [])
  const services = useApi(() => opsApi.getServices(), [])
  const checks = useApi(() => opsApi.getHealthChecks(), [])
  const assets = useApi(() => opsApi.getDataAssets(), [])

  const items = runs.data?.items ?? []
  const dbOk = health.data?.db === 'ok'

  // 任务时间线：默认仅展示最近一天，点击展开全部（runs 按 run_date 倒序返回）
  const [expanded, setExpanded] = useState(false)
  const firstDay = items[0]?.run_date ?? ''
  const visible = expanded ? items : items.filter(it => it.run_date === firstDay)
  const dayCount = new Set(items.map(it => it.run_date)).size

  // 告警记录：失败任务 + alert: 类红卡告警（「该做没做」/ 任务失败都落 task_run 台账）
  const alerts = items.filter(it => it.status === 'failed' || it.task_name.startsWith('alert:'))

  return (
    <section className="page">
      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr 1fr', marginBottom: 14 }}>
        <div className="card">
          <h3>
            任务执行时间线
            <span className="hint">
              {health.data?.time ? `${health.data.time.slice(0, 16).replace('T', ' ')} 实时` : '最多 100 条'}
            </span>
          </h3>
          {runs.error ? (
            <Notice text={runs.error} onRetry={runs.reload} retrying={runs.loading} />
          ) : runs.loading && !runs.data ? (
            <div className="empty">任务记录加载中…</div>
          ) : items.length === 0 ? (
            <div className="empty">暂无任务记录</div>
          ) : (
            <>
              <div className="tl">
                {visible.map(item => {
                  const [statusText, cls] = STATUS_TEXT[item.status] ?? [item.status, '']
                  const tm = item.created_at || item.run_date
                  return (
                    <div className={`tl-item ${cls}`} key={item.id}>
                      <div className="t">
                        {tm.slice(5, 16)} · {statusText}
                      </div>
                      <div className="n">{fmtTask(item.task_name)}</div>
                      {item.message && (
                        <div style={{ fontSize: 13.5, color: 'var(--txt3)' }}>{item.message}</div>
                      )}
                    </div>
                  )
                })}
              </div>
              {dayCount > 1 && (
                <div className="tl-more" onClick={() => setExpanded(e => !e)}>
                  {expanded
                    ? `收起 · 仅显示 ${firstDay}`
                    : `展开全部 · 近 ${dayCount} 天 · 共 ${items.length} 条`}
                </div>
              )}
            </>
          )}
        </div>

        <div className="card">
          <h3>
            服务状态<span className="hint">6 容器实时探活</span>
          </h3>
          {services.error ? (
            <Notice text={services.error} onRetry={services.reload} retrying={services.loading} />
          ) : services.loading && !services.data ? (
            <div className="empty">服务状态加载中…</div>
          ) : (services.data?.items ?? []).length === 0 ? (
            <div className="empty">暂无服务信息</div>
          ) : (
            <>
              {(services.data?.items ?? []).map(s => (
                <ServiceRow key={s.name} svc={s} />
              ))}
              <div
                style={{
                  marginTop: 10,
                  fontSize: 13,
                  color: 'var(--txt3)',
                  borderTop: '1px solid var(--line)',
                  paddingTop: 10,
                }}
              >
                {dbOk
                  ? '后端实时探测容器状态 · 悬停可见详情'
                  : '数据库不可达（看后端日志）'}
              </div>
            </>
          )}
        </div>

        <div className="card">
          <h3>
            告警记录<span className="hint">由任务台账过滤</span>
          </h3>
          {runs.error ? (
            <Notice text={runs.error} onRetry={runs.reload} retrying={runs.loading} />
          ) : runs.loading && !runs.data ? (
            <div className="empty">告警记录加载中…</div>
          ) : alerts.length === 0 ? (
            <div className="empty" style={{ padding: '56px 0' }}>
              暂无告警 ✅（失败任务与红卡告警会出现在这里）
            </div>
          ) : (
            <div className="tl">
              {alerts.map(it => {
                const tm = it.created_at || it.run_date
                return (
                  <div className="tl-item fail" key={it.id}>
                    <div className="t">
                      {tm.slice(5, 16)} · {it.status === 'failed' ? '任务失败' : '告警'}
                    </div>
                    <div className="n">{fmtTask(it.task_name)}</div>
                    {it.message && (
                      <div style={{ fontSize: 13.5, color: 'var(--txt3)' }}>{it.message}</div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="card">
          <h3>
            数据健康检查
            <span className="hint">
              {checks.data?.date ? `最近一次 · ${checks.data.date}` : '每日 7 项体检'}
            </span>
          </h3>
          {checks.error ? (
            <Notice text={checks.error} onRetry={checks.reload} retrying={checks.loading} />
          ) : checks.loading && !checks.data ? (
            <div className="empty">数据健康加载中…</div>
          ) : (checks.data?.items ?? []).length === 0 ? (
            <div className="empty">暂无检查记录</div>
          ) : (
            <HealthChecks items={checks.data?.items ?? []} />
          )}
        </div>

        <div className="card">
          <h3>
            数据资产概览<span className="hint">数据即地基</span>
          </h3>
          {assets.error ? (
            <Notice text={assets.error} onRetry={assets.reload} retrying={assets.loading} />
          ) : assets.loading && !assets.data ? (
            <div className="empty">数据资产加载中…</div>
          ) : (assets.data?.items ?? []).length === 0 ? (
            <div className="empty">暂无数据资产</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>数据表</th>
                  <th className="r">行数</th>
                </tr>
              </thead>
              <tbody>
                {(assets.data?.items ?? []).map(a => (
                  <tr key={a.table}>
                    <td className="num">{a.table}</td>
                    <td className="r num">{a.rows.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  )
}
