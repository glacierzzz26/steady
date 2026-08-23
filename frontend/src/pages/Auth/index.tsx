import Tag from '../../components/Tag'

const accessModel = [
  { cat: '只读浏览', example: '看盘 / 信号 / 回测结果', auth: '无需登录（内网）', extra: '—' },
  { cat: '研究操作', example: '新建因子 / 策略 / 提交回测', auth: '登录', extra: '—' },
  { cat: '配置变更', example: '数据源 / 通知 / 调度时间', auth: '登录', extra: '审计' },
  { cat: '模拟交易', example: '手动执行 execute-day', auth: '登录', extra: '确认弹窗' },
  { cat: '实盘敏感', example: '实盘开关 / 停机 / 手动下单', auth: '登录', extra: '2FA + 审计', danger: true },
]

const session = [
  ['管理员', '单账户'],
  ['会话有效期', '12h'],
  ['同时在线', '单设备'],
  ['记住设备', '30 天'],
]

const auditLogs = [
  { time: '08-21 19:32', user: 'admin', action: '发布因子 roe_quality v1.1', ip: '192.168.0.100', ok: true },
  { time: '08-21 14:05', user: 'admin', action: '提交回测任务 #11', ip: '192.168.0.100', ok: true },
  { time: '08-20 09:12', user: 'admin', action: '修改飞书通知配置', ip: '192.168.0.201', ok: true },
  { time: '08-19 21:40', user: 'admin', action: '手动执行 execute-day', ip: '192.168.0.100', ok: true },
]

const principles = [
  '① 单管理员，不做多角色 — 个人系统的需求是可追溯，不是权限树',
  '② 密码 Argon2id 哈希存储；2FA 基于 TOTP，兼容主流验证器',
  '③ 会话绑定内网网段，外网访问直接拒绝',
  '④ 全部写操作 POST 带 CSRF token，防跨站请求伪造',
  '⑤ 现有飞书 webhook 签名校验保留；实盘告警走独立通知渠道，与日常通知隔离',
  '⑥ 审计日志只增不改（append-only），数据库定期冷备',
]

export default function Auth() {
  return (
    <section className="page">
      <div className="banner">
        <b>Phase 3 概念设计 · 本页未实现。</b>
        定位：单管理员 + 敏感操作审计，不做多角色权限树。登录体系随实盘一起建设 —
        它存在的意义是让真钱操作可追溯，而非团队协作。
      </div>

      <div className="grid" style={{ gridTemplateColumns: '380px 1fr', marginBottom: 14 }}>
        {/* 登录页设计稿 */}
        <div className="card">
          <h3>
            登录页设计稿<span className="hint">全屏居中 · 深色</span>
          </h3>
          <div className="lg-wrap">
            <div className="lg-card">
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 4 }}>
                <div className="logo-mark">S</div>
                <b style={{ fontSize: 16 }}>Steady Quant</b>
              </div>
              <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 18 }}>个人量化研究终端 · 管理员登录</div>
              <div style={{ fontSize: 13, color: 'var(--txt2)' }}>用户名</div>
              <input className="lg-in" type="text" defaultValue="admin" />
              <div style={{ fontSize: 13, color: 'var(--txt2)', marginTop: 12 }}>密码</div>
              <input className="lg-in" type="text" defaultValue="••••••••••" />
              <div style={{ fontSize: 13, color: 'var(--txt2)', marginTop: 12 }}>动态验证码 (2FA)</div>
              <input className="lg-in" type="text" defaultValue="318 204" />
              <button className="btn pri" style={{ width: '100%', justifyContent: 'center', marginTop: 16 }} disabled>
                登 录
              </button>
              <div style={{ fontSize: 12.5, color: 'var(--txt3)', textAlign: 'center', marginTop: 12 }}>
                仅内网可访问 · 连续失败 5 次锁定 15 分钟
              </div>
            </div>
          </div>
        </div>

        {/* 访问控制模型 */}
        <div className="card">
          <h3>
            访问控制模型<span className="hint">按敏感度分级，而非按角色分权</span>
          </h3>
          <table>
            <thead>
              <tr>
                <th>操作类别</th>
                <th>示例</th>
                <th>认证</th>
                <th>额外防线</th>
              </tr>
            </thead>
            <tbody>
              {accessModel.map(m => (
                <tr key={m.cat}>
                  <td style={m.danger ? { color: 'var(--up)' } : undefined}>{m.cat}</td>
                  <td style={{ color: 'var(--txt2)', fontSize: 13.5 }}>{m.example}</td>
                  <td>{m.auth}</td>
                  <td style={m.danger ? { color: 'var(--up)' } : undefined}>{m.extra}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 14 }}>
            <div>
              <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 4 }}>会话管理</div>
              <table style={{ fontSize: 14 }}>
                <tbody>
                  {session.map(s => (
                    <tr key={s[0]}>
                      <td style={{ color: 'var(--txt2)' }}>{s[0]}</td>
                      <td className="r num">{s[1]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <div style={{ fontSize: 13, color: 'var(--txt3)', marginBottom: 4 }}>架构升级要点</div>
              <div style={{ fontSize: 14, color: 'var(--txt2)', lineHeight: 1.9 }}>
                现有「API 无鉴权 + 内网兜底」升级为
                <b style={{ color: 'var(--txt)' }}>「读开放 · 写鉴权」</b>
                ：GET 保持免登录快速看盘，全部 POST 走 session + CSRF token，nginx 层可叠加 IP 白名单。
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="card">
          <h3>
            审计日志<span className="hint">示例数据 · 实盘操作将重点标记</span>
          </h3>
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>账户</th>
                <th>动作</th>
                <th>来源</th>
                <th className="r">结果</th>
              </tr>
            </thead>
            <tbody>
              {auditLogs.map(l => (
                <tr key={l.time}>
                  <td className="num">{l.time}</td>
                  <td className="num">{l.user}</td>
                  <td>{l.action}</td>
                  <td className="num" style={{ color: 'var(--txt3)' }}>
                    {l.ip}
                  </td>
                  <td className="r">
                    <Tag type="ok" label="成功" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h3>安全设计原则</h3>
          <div style={{ fontSize: 14.5, color: 'var(--txt2)', lineHeight: 2.1 }}>
            {principles.map(p => (
              <div key={p}>{p}</div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
