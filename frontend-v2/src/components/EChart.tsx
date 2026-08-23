import { useEffect, useRef } from 'react'
import type { CSSProperties } from 'react'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'

interface Props {
  option: EChartsOption
  height?: number | string
  style?: CSSProperties
}

/** ECharts 通用封装：init / setOption / resize / dispose */
export default function EChart({ option, height = 260, style }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    chartRef.current = echarts.init(ref.current)
    const onResize = () => chartRef.current?.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option)
    chartRef.current?.resize()
  }, [option])

  return <div ref={ref} className="chart" style={{ height, ...style }} />
}
