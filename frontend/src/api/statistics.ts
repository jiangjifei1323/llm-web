import { getToken, handleUnauthorized } from './auth'
import { requestJson } from './http'

/** 统计周期类型 */
export type PeriodType = 'day' | 'week' | 'month'

/** 单个统计项 */
export interface StatisticsItem {
  time: string  // 时间标签（小时或日期）
  input_tokens: number
  output_tokens: number
  total_tokens: number
  call_count: number
}

/** 汇总统计 */
export interface StatisticsSummary {
  total_input: number
  total_output: number
  total_tokens: number
  total_calls: number
}

/** 统计响应 */
export interface StatisticsResponse {
  period: string
  unit: string  // "hour" 或 "day"
  data: StatisticsItem[]
  summary: StatisticsSummary
}

/** 获取 Token 用量统计 */
export function fetchTokenStatistics(period: PeriodType = 'day', date: string = ''): Promise<StatisticsResponse> {
  const params = new URLSearchParams({ period })
  if (date) params.append('date', date)
  return requestJson<StatisticsResponse>(`/api/admin/statistics/token?${params.toString()}`)
}
