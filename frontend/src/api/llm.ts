/** LLM 助手 domain：术语解释 / 项目问答（契约 §4.13） */
import { http } from './http'
import type { ProjectAnswer, TermExplanation } from './types'

export const llmApi = {
  explainTerm: (term: string) => http.post<TermExplanation>('/llm/glossary', { term }),
  askProject: (question: string) => http.post<ProjectAnswer>('/llm/ask', { question }),
}
