<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'

import { fetchTokenStatistics, type PeriodType, type StatisticsResponse } from '@/api/statistics'

const loading = ref(false)
const period = ref<PeriodType>('day')
const chartData = ref<StatisticsResponse | null>(null)

let chartInstance: echarts.ECharts | null = null

const summary = computed(() => chartData.value?.summary ?? null)
const data = computed(() => chartData.value?.data ?? [])

function formatNumber(num: number): string {
  if (num >= 1_000_000) return (num / 1_000_000).toFixed(2) + 'M'
  if (num >= 1_000) return (num / 1_000).toFixed(1) + 'K'
  return num.toString()
}

async function loadData() {
  loading.value = true
  try {
    chartData.value = await fetchTokenStatistics(period.value)
    updateChart()
  } catch (err) {
    ElMessage.error('加载统计数据失败')
  } finally {
    loading.value = false
  }
}

function switchPeriod(p: PeriodType) {
  period.value = p
  void loadData()
}

function updateChart() {
  if (!chartInstance || !chartData.value) return

  const xLabels = chartData.value.data.map((d) => d.time)
  const inputTokens = chartData.value.data.map((d) => d.input_tokens)
  const outputTokens = chartData.value.data.map((d) => d.output_tokens)
  const callCounts = chartData.value.data.map((d) => d.call_count)

  chartInstance.setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
    },
    legend: {
      data: ['输入 Token', '输出 Token', '调用次数'],
      bottom: 0,
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '10%',
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: xLabels,
      axisLabel: {
        rotate: period.value === 'day' ? 0 : 45,
      },
    },
    yAxis: [
      {
        type: 'value',
        name: 'Token 数量',
        axisLabel: {
          formatter: (value: number) => formatNumber(value),
        },
      },
      {
        type: 'value',
        name: '调用次数',
        axisLabel: {
          formatter: (value: number) => value.toString(),
        },
      },
    ],
    series: [
      {
        name: '输入 Token',
        type: 'bar',
        stack: 'total',
        emphasis: { focus: 'series' },
        itemStyle: { color: '#5470c6' },
        data: inputTokens,
      },
      {
        name: '输出 Token',
        type: 'bar',
        stack: 'total',
        emphasis: { focus: 'series' },
        itemStyle: { color: '#91cc75' },
        data: outputTokens,
      },
      {
        name: '调用次数',
        type: 'line',
        yAxisIndex: 1,
        symbol: 'circle',
        symbolSize: 6,
        itemStyle: { color: '#fac858' },
        data: callCounts,
      },
    ],
  })
}

onMounted(() => {
  const chartEl = document.getElementById('token-chart')
  if (chartEl) {
    chartInstance = echarts.init(chartEl)
    window.addEventListener('resize', () => chartInstance?.resize())
  }
  void loadData()
})

// 组件卸载时清理
import { onUnmounted } from 'vue'
onUnmounted(() => {
  chartInstance?.dispose()
})
</script>

<template>
  <div class="token-statistics">
    <!-- 周期切换 -->
    <div class="period-tabs">
      <button :class="{ active: period === 'day' }" @click="switchPeriod('day')">日</button>
      <button :class="{ active: period === 'week' }" @click="switchPeriod('week')">周</button>
      <button :class="{ active: period === 'month' }" @click="switchPeriod('month')">月</button>
    </div>

    <!-- 汇总卡片 -->
    <div v-if="summary" class="summary-cards">
      <div class="card">
        <div class="card-label">总输入 Token</div>
        <div class="card-value input">{{ formatNumber(summary.total_input) }}</div>
      </div>
      <div class="card">
        <div class="card-label">总输出 Token</div>
        <div class="card-value output">{{ formatNumber(summary.total_output) }}</div>
      </div>
      <div class="card">
        <div class="card-label">总 Token</div>
        <div class="card-value total">{{ formatNumber(summary.total_tokens) }}</div>
      </div>
      <div class="card">
        <div class="card-label">调用次数</div>
        <div class="card-value calls">{{ summary.total_calls }}</div>
      </div>
    </div>

    <!-- 图表 -->
    <div v-loading="loading" class="chart-container">
      <div id="token-chart" class="chart"></div>
    </div>
  </div>
</template>

<style scoped>
.token-statistics {
  padding: 16px;
}

.period-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.period-tabs button {
  padding: 6px 16px;
  border: 1px solid #d0d0d0;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.2s;
}

.period-tabs button:hover {
  border-color: #1a7f37;
  color: #1a7f37;
}

.period-tabs button.active {
  background: #1a7f37;
  color: #fff;
  border-color: #1a7f37;
}

.summary-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 20px;
}

.card {
  padding: 16px;
  border-radius: 8px;
  background: #f8f9fa;
  text-align: center;
}

.card-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.card-value {
  font-size: 24px;
  font-weight: 600;
}

.card-value.input { color: #5470c6; }
.card-value.output { color: #91cc75; }
.card-value.total { color: #1a7f37; }
.card-value.calls { color: #fac858; }

.chart-container {
  min-height: 400px;
  border: 1px solid #e5e5e5;
  border-radius: 8px;
  padding: 16px;
  background: #fff;
}

.chart {
  width: 100%;
  height: 400px;
}
</style>
