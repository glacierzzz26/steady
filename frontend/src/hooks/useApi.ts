/**
 * useApi —— 数据请求 hook。
 * - deps 变化自动重取；`reload()` 手动重跑
 * - 40004（资源缺失）→ 空态（error 置空，由页面显示空态）；其余错误 → error 字符串，页面渲染 Notice + 重试
 * - 重取时保留旧数据避免闪烁
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api'

export interface UseApiState<T> {
  data?: T
  error?: string
  code?: number // 后端业务码；0 = 网络/HTTP 错误
  loading: boolean
}

const NETWORK_MSG = '网络异常，请稍后重试'

export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []): UseApiState<T> & { reload: () => void } {
  const [state, setState] = useState<UseApiState<T>>({ loading: true })
  const fnRef = useRef(fetcher)
  fnRef.current = fetcher

  const run = useCallback(async () => {
    setState(s => ({ ...s, loading: true, error: undefined, code: undefined }))
    try {
      const data = await fnRef.current()
      setState({ data, loading: false })
    } catch (e) {
      if (e instanceof ApiError) {
        // 资源缺失 → 空态而非错误（页面按 data 空处理）
        if (e.code === 40004) setState({ data: undefined, loading: false })
        else setState({ error: e.message, code: e.code, loading: false })
      } else {
        setState({ error: NETWORK_MSG, code: 0, loading: false })
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    run()
  }, [run])

  return { ...state, reload: run }
}
