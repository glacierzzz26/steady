/** 早盘简报 domain（契约 §4.11 / 4.13） */
import { http } from './http'
import type { BriefInterpretation, MorningBriefData } from './types'

export const briefApi = {
  /** date 缺省返回最近一份 */
  getMorningBrief: (date?: string) => http.get<MorningBriefData>('/morning-brief', { date }),
  /** briefDate 留空 = 最近一份早报 */
  interpretBrief: (briefDate?: string) =>
    http.post<BriefInterpretation>('/llm/interpret-brief', { brief_date: briefDate ?? '' }),
}
