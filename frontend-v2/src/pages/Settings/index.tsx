import { useEffect, useState } from 'react'
import Notice from '../../components/Notice'
import Tag from '../../components/Tag'
import { settingsApi } from '../../api'
import { useApi } from '../../hooks/useApi'
import type { FeishuConfig, LLMConfig, LLMConfigUpdate, NotifyEvent } from '../../api'

interface Msg {
  ok: boolean
  text: string
}

const PROV_CN: Record<string, string> = {
  openai: 'OpenAI',
  deepseek: 'DeepSeek',
  qwen: '通义千问 (Qwen)',
  glm: '智谱 GLM',
}

function InlineMsg({ m }: { m: Msg | null }) {
  if (!m) return null
  return (
    <div style={{ fontSize: 12.5, color: m.ok ? 'var(--ok)' : 'var(--up)', marginTop: 10 }}>
      {m.ok ? '✓ ' : '✗ '}
      {m.text}
    </div>
  )
}

export default function Settings() {
  // —— 加载：三卡独立拉取（各自出错各自 Notice+重试）——
  const tushare = useApi(() => settingsApi.getTushareConfig(), [])
  const notify = useApi(() => settingsApi.getNotifyConfig(), [])
  const llm = useApi(() => settingsApi.getLLMConfig(), [])

  // —— 本地编辑态（数据到达后从 data 拷贝）——
  const [tk, setTk] = useState('')
  const [tMsg, setTMsg] = useState<Msg | null>(null)
  const [feishu, setFeishu] = useState<FeishuConfig | null>(null)
  const [events, setEvents] = useState<NotifyEvent[]>([])
  const [nMsg, setNMsg] = useState<Msg | null>(null)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [llmCfg, setLlmCfg] = useState<LLMConfig | null>(null)
  const [llmKey, setLlmKey] = useState('')
  const [lMsg, setLMsg] = useState<Msg | null>(null)

  useEffect(() => {
    if (notify.data) {
      setFeishu(notify.data.feishu)
      setEvents(notify.data.events)
    }
  }, [notify.data])

  useEffect(() => {
    if (llm.data) setLlmCfg(llm.data)
  }, [llm.data])

  const [savingTushare, setSavingTushare] = useState(false)
  const [testingTushare, setTestingTushare] = useState(false)
  const [savingNotify, setSavingNotify] = useState(false)
  const [testingNotify, setTestingNotify] = useState(false)
  const [savingLLM, setSavingLLM] = useState(false)
  const [testingLLM, setTestingLLM] = useState(false)

  // ---- Tushare ----
  const onSaveTushare = async () => {
    const token = tk.trim()
    if (!token) return // 空输入不提交，避免误清空
    setSavingTushare(true)
    try {
      await settingsApi.updateTushareConfig(token)
      setTk('')
      setTMsg({ ok: true, text: '已保存（Token 完整值不回显）' })
    } catch (e) {
      setTMsg({ ok: false, text: e instanceof Error ? e.message : '保存失败' })
    } finally {
      setSavingTushare(false)
    }
  }

  const onTestTushare = async () => {
    setTestingTushare(true)
    try {
      await settingsApi.testTushare(tk.trim()) // token 留空 = 测已存 token
      setTMsg({ ok: true, text: 'Tushare 连接正常' })
    } catch (e) {
      setTMsg({ ok: false, text: e instanceof Error ? e.message : '连接失败' })
    } finally {
      setTestingTushare(false)
    }
  }

  // ---- 飞书：事件开关 + 整体配置 ----
  const patchEvent = (eventKey: string, patch: Partial<NotifyEvent>) =>
    setEvents(prev => prev.map(e => (e.event_key === eventKey ? { ...e, ...patch } : e)))

  const onSaveEvent = async (ev: NotifyEvent) => {
    setSavingKey(ev.event_key)
    try {
      await settingsApi.updateNotifyEvent(ev.event_key, ev)
      setNMsg({ ok: true, text: `${ev.name} 已保存` })
    } catch (e) {
      setNMsg({ ok: false, text: e instanceof Error ? e.message : '保存失败' })
    } finally {
      setSavingKey(null)
    }
  }

  const onSaveFeishu = async () => {
    if (!feishu) return
    setSavingNotify(true)
    try {
      await settingsApi.updateFeishuConfig(feishu)
      setNMsg({ ok: true, text: '飞书配置已保存' })
    } catch (e) {
      setNMsg({ ok: false, text: e instanceof Error ? e.message : '保存失败' })
    } finally {
      setSavingNotify(false)
    }
  }

  const onTestNotify = async () => {
    setTestingNotify(true)
    try {
      await settingsApi.sendNotifyTest()
      setNMsg({ ok: true, text: '测试卡片已发送，请查看飞书群' })
    } catch (e) {
      setNMsg({ ok: false, text: e instanceof Error ? e.message : '发送失败' })
    } finally {
      setTestingNotify(false)
    }
  }

  // ---- LLM：api_key 留空 = 保留已存 ----
  const onSaveLLM = async () => {
    if (!llmCfg) return
    setSavingLLM(true)
    try {
      const req: LLMConfigUpdate = {
        enabled: llmCfg.enabled,
        provider: llmCfg.provider,
        model: llmCfg.model.trim(),
        base_url: llmCfg.base_url.trim(),
      }
      const key = llmKey.trim()
      if (key) req.api_key = key
      await settingsApi.updateLLMConfig(req)
      setLlmCfg({ ...llmCfg, api_key_masked: key ? `****${key.slice(-4)}` : llmCfg.api_key_masked })
      setLlmKey('')
      setLMsg({ ok: true, text: '大模型配置已保存' })
    } catch (e) {
      setLMsg({ ok: false, text: e instanceof Error ? e.message : '保存失败' })
    } finally {
      setSavingLLM(false)
    }
  }

  const onTestLLM = async () => {
    setTestingLLM(true)
    try {
      await settingsApi.testLLM()
      setLMsg({ ok: true, text: '大模型连接正常' })
    } catch (e) {
      setLMsg({ ok: false, text: e instanceof Error ? e.message : '连接失败' })
    } finally {
      setTestingLLM(false)
    }
  }

  return (
    <section className="page">
      <div className="grid" style={{ gridTemplateColumns: 'repeat(3,1fr)' }}>
        {/* ---- Tushare ---- */}
        <div className="card">
          <h3>数据源 · Tushare</h3>
          {tushare.error ? (
            <Notice text={tushare.error} onRetry={tushare.reload} retrying={tushare.loading} />
          ) : (
            <>
              <div style={{ fontSize: 14, color: 'var(--txt2)', marginBottom: 6 }}>Token</div>
              <input
                type="text"
                value={tk}
                onChange={e => setTk(e.target.value)}
                placeholder={tushare.data?.configured ? `已配置 ${tushare.data.token_masked}，留空保持` : '粘贴 Tushare Pro token'}
                style={{ width: '100%' }}
              />
              <div style={{ fontSize: 14, color: 'var(--txt2)', margin: '12px 0 6px' }}>当前状态</div>
              <div style={{ fontSize: 14.5, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {tushare.data?.configured ? (
                  <Tag type="ok" label={`主源已配置 ${tushare.data.token_masked}`} />
                ) : (
                  <Tag type="warn" label="未配置 · 采集走 AkShare" />
                )}
                <Tag type="hold" label="AkShare 兜底待命" />
              </div>
              <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
                <button className="btn" onClick={onTestTushare} disabled={testingTushare}>
                  {testingTushare ? '测试中…' : '测试连接'}
                </button>
                <button className="btn pri" onClick={onSaveTushare} disabled={savingTushare || !tk.trim()}>
                  {savingTushare ? '保存中…' : '保存'}
                </button>
              </div>
              <InlineMsg m={tMsg} />
            </>
          )}
        </div>

        {/* ---- 飞书 ---- */}
        <div className="card">
          <h3>通知 · 飞书机器人</h3>
          {notify.error ? (
            <Notice text={notify.error} onRetry={notify.reload} retrying={notify.loading} />
          ) : (
            <>
              <div style={{ fontSize: 14, color: 'var(--txt2)', marginBottom: 6 }}>推送项</div>
              {events.length === 0 ? (
                <div className="empty">暂无通知事件</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {events.map(ev => (
                    <div key={ev.event_key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 13.5, flex: 1 }}>{ev.name}</span>
                      <input
                        type="checkbox"
                        checked={ev.enabled}
                        onChange={e => patchEvent(ev.event_key, { enabled: e.target.checked })}
                      />
                      <button
                        className="btn"
                        style={{ padding: '2px 8px', fontSize: 12 }}
                        onClick={() => onSaveEvent(ev)}
                        disabled={savingKey === ev.event_key}
                      >
                        {savingKey === ev.event_key ? '保存中' : '保存'}
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <div style={{ fontSize: 14, color: 'var(--txt2)', margin: '14px 0 6px' }}>Webhook</div>
              <input
                type="text"
                value={feishu?.webhook_url ?? ''}
                onChange={e => feishu && setFeishu({ ...feishu, webhook_url: e.target.value })}
                placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                style={{ width: '100%' }}
              />
              <div style={{ fontSize: 14, color: 'var(--txt2)', margin: '10px 0 6px' }}>
                启用通知
                <input
                  type="checkbox"
                  checked={feishu?.enabled ?? false}
                  onChange={e => feishu && setFeishu({ ...feishu, enabled: e.target.checked })}
                  style={{ marginLeft: 8, verticalAlign: 'middle' }}
                />
              </div>
              <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="btn" onClick={onTestNotify} disabled={testingNotify}>
                  {testingNotify ? '发送中…' : '发送测试卡片'}
                </button>
                <button className="btn pri" onClick={onSaveFeishu} disabled={savingNotify}>
                  {savingNotify ? '保存中…' : '保存飞书配置'}
                </button>
              </div>
              <InlineMsg m={nMsg} />
            </>
          )}
        </div>

        {/* ---- LLM ---- */}
        <div className="card">
          <h3>AI 助手 · LLM</h3>
          {llm.error ? (
            <Notice text={llm.error} onRetry={llm.reload} retrying={llm.loading} />
          ) : (
            <>
              <table style={{ fontSize: 14 }}>
                <tbody>
                  <tr>
                    <td style={{ color: 'var(--txt2)' }}>Provider</td>
                    <td className="r">
                      <select
                        style={{ padding: '4px 8px' }}
                        value={llmCfg?.provider ?? 'openai'}
                        onChange={e => llmCfg && setLlmCfg({ ...llmCfg, provider: e.target.value as LLMConfig['provider'] })}
                      >
                        {Object.entries(PROV_CN).map(([v, l]) => (
                          <option key={v} value={v}>
                            {l}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                  <tr>
                    <td style={{ color: 'var(--txt2)' }}>模型</td>
                    <td className="r">
                      <input
                        type="text"
                        value={llmCfg?.model ?? ''}
                        onChange={e => llmCfg && setLlmCfg({ ...llmCfg, model: e.target.value })}
                        placeholder="deepseek-chat"
                        style={{ width: '100%' }}
                      />
                    </td>
                  </tr>
                  <tr>
                    <td style={{ color: 'var(--txt2)' }}>Base URL</td>
                    <td className="r">
                      <input
                        type="text"
                        value={llmCfg?.base_url ?? ''}
                        onChange={e => llmCfg && setLlmCfg({ ...llmCfg, base_url: e.target.value })}
                        placeholder="留空用默认"
                        style={{ width: '100%' }}
                      />
                    </td>
                  </tr>
                  <tr>
                    <td style={{ color: 'var(--txt2)' }}>API Key</td>
                    <td className="r">
                      <input
                        type="text"
                        value={llmKey}
                        onChange={e => setLlmKey(e.target.value)}
                        placeholder={
                          llmCfg?.api_key_masked
                            ? `已配置 ${llmCfg.api_key_masked}，留空保持`
                            : '粘贴 API Key'
                        }
                        style={{ width: '100%' }}
                      />
                    </td>
                  </tr>
                  <tr>
                    <td style={{ color: 'var(--txt2)' }}>数据访问</td>
                    <td className="r">
                      <Tag type="ok" label="只读白名单" />
                    </td>
                  </tr>
                </tbody>
              </table>
              <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
                <button className="btn" onClick={onTestLLM} disabled={testingLLM}>
                  {testingLLM ? '测试中…' : '测试对话'}
                </button>
                <button className="btn pri" onClick={onSaveLLM} disabled={savingLLM}>
                  {savingLLM ? '保存中…' : '保存'}
                </button>
              </div>
              <InlineMsg m={lMsg} />
            </>
          )}
        </div>
      </div>
    </section>
  )
}
