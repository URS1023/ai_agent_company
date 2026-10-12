<template>
  <div class="data-analysis-dashboard">
    <p class="dashboard-scope">{{ truncated ? '当前为截断后的返回样本' : '统计范围：本次查询返回结果' }}。无历史同期数据时不计算增长率。</p>
    <!-- 顶部标题栏 -->
    <div class="dashboard-header">
      <div class="header-title">
        <span class="title-icon">📊</span>
        <span class="title-text">数据分析驾驶舱</span>
      </div>
      <div class="header-time">{{ currentTime }}</div>
    </div>

    <!-- 顶部指标卡片区域 -->
    <div class="metrics-section">
      <div class="metric-card" v-for="(metric, index) in metrics" :key="index" :class="`metric-${index}`">
        <div class="metric-icon">{{ metric.icon }}</div>
        <div class="metric-content">
          <div class="metric-value" :title="metric.title ?? String(metric.value)">{{ metric.value }}</div>
          <div class="metric-label">{{ metric.label }}</div>
          <div v-if="metric.change" class="metric-trend" :class="metric.trend">
            <span class="trend-arrow">{{ metric.trend === 'up' ? '▲' : '▼' }}</span>
            {{ metric.change }}
          </div>
        </div>
        <div v-if="metric.progress != null" class="metric-progress">
          <div class="progress-bar" :style="{ width: metric.progress + '%' }"></div>
        </div>
      </div>
    </div>

    <!-- 主要内容区域 -->
    <div class="main-content">
      <!-- 左侧区域 -->
      <div class="left-section">
        <!-- 左上：柱状图 -->
        <div class="chart-box">
          <div class="chart-header">
            <span class="chart-title">{{ leftChartTitle }}</span>
            <div class="chart-actions">
              <button type="button" class="action-btn" aria-label="根据当前查询结果重新绘制图表" @click="refreshChart('left')">🔄</button>
            </div>
          </div>
          <div ref="leftChartRef" class="chart-container"></div>
        </div>

        <!-- 左下：雷达图 -->
        <div class="chart-box">
          <div class="chart-header">
            <span class="chart-title">数值字段均值（各轴独立刻度）</span>
          </div>
          <div ref="radarChartRef" class="chart-container"></div>
        </div>
      </div>

      <!-- 中间区域 -->
      <div class="center-section">
        <!-- 中间顶部：数字滚动 -->
        <div class="center-top">
          <div class="big-number-box">
            <div class="big-number">{{ mainNumber }}</div>
            <div class="big-number-label">本次返回记录数</div>
          </div>
        </div>

        <!-- 中间：环形图 -->
        <div class="chart-box center-chart">
          <div class="chart-header">
            <span class="chart-title">{{ rightChartTitle }}</span>
          </div>
          <div ref="rightChartRef" class="chart-container"></div>
        </div>

        <!-- 中间底部：进度条 -->
        <div class="progress-section">
          <div class="progress-item" v-for="(item, index) in progressItems" :key="index">
            <div class="progress-label">{{ item.label }}</div>
            <div class="progress-bar-container">
              <div class="progress-bar" :style="{ width: item.value + '%', background: item.color }"></div>
            </div>
            <div class="progress-value">{{ item.value }}%</div>
          </div>
        </div>
      </div>

      <!-- 右侧区域 -->
      <div class="right-section">
        <!-- 右上：折线图 -->
        <div class="chart-box">
          <div class="chart-header">
            <span class="chart-title">{{ bottomChartTitle }}</span>
          </div>
          <div ref="bottomChartRef" class="chart-container"></div>
        </div>

        <!-- 右下：数据列表 -->
        <div class="chart-box data-list-box">
          <div class="chart-header">
            <span class="chart-title">分类频次 TOP 10</span>
          </div>
          <div class="data-list">
            <div class="data-list-item" v-for="(item, index) in topDataList" :key="index">
              <div class="item-rank" :class="`rank-${index + 1}`">{{ index + 1 }}</div>
              <div class="item-name">{{ item.name }}</div>
              <div class="item-value">{{ item.value }}</div>
              <div class="item-bar">
                <div class="item-bar-inner" :style="{ width: item.percent + '%' }"></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部数据表格 -->
    <div class="table-section">
      <div class="chart-box">
        <div class="chart-header">
          <span class="chart-title">详细数据列表</span>
          <div class="table-pagination">
            <span>共 {{ tableData.length }} 条</span>
          </div>
        </div>
        <el-table :data="tableData" stripe style="width: 100%" height="250">
          <el-table-column 
            v-for="col in inferredColumns" 
            :key="col.columnName"
            :prop="col.columnName"
            :label="col.comment || col.columnName"
            :min-width="120"
          />
        </el-table>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 数据分析概览大屏（指标卡 + 四象限图表 + 明细表，由查询结果派生）。
 * 路由：无独立路由；由 QueryResultPanel 的"大屏"页签渲染，与查询结果共用同一份 data/statistics。
 * 依赖接口：无请求（数据由父组件传入）；图表 option 经 utils/safeContent.ts 的 safeChartOption 过滤后交给 ECharts。
 * 关键交互：数据变化时重新分析列结构、重算指标与排行榜并重建各图；窗口尺寸变化时统一 resize。
 * 约束：列信息缺失时从首行推断（inferDataType），推断结果只影响展示，不修改原始数据；
 *   ECharts 实例在卸载时 dispose（避免内存泄漏），图表容器用 v-show 保持实例有效。
 */
import { ref, onMounted, onUnmounted, watch, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import { safeChartOption } from '@/utils/safeContent'
import { formatDecimalText as formatNumber, decimalValueTitle } from '../utils/decimalDisplay'

const props = defineProps({
  statistics: { type: Object, default: () => ({}) },
  truncated: { type: Boolean, default: false },
  data: {
    type: Array,
    required: true
  },
  columns: {
    type: Array,
    default: () => []
  }
})

// 从数据中推断列信息
const inferredColumns = computed(() => {
  if (props.columns && props.columns.length > 0) {
    return props.columns
  }
  
  // 如果没有提供columns，从数据中推断
  if (props.data && props.data.length > 0) {
    const firstRow = props.data[0]
    return Object.keys(firstRow).map(key => ({
      columnName: key,
      comment: key,
      dataType: inferDataType(firstRow[key])
    }))
  }
  
  return []
})

// 推断数据类型
/**
 * 推断字段类型：仅用于渲染（数字/布尔/日期/文本），日期识别依赖 Date.parse 的宽松解析。
 * 约束：推断结果不得回写后端或用于业务判定，真正的类型以数据源元数据为准。
 */
const inferDataType = (value) => {
  if (typeof value === 'number') {
    return Number.isInteger(value) ? 'int' : 'decimal'
  }
  if (typeof value === 'boolean') {
    return 'boolean'
  }
  if (value instanceof Date) {
    return 'datetime'
  }
  // 检查是否是日期字符串
  if (typeof value === 'string' && !isNaN(Date.parse(value))) {
    return 'datetime'
  }
  return 'varchar'
}

const leftChartRef = ref(null)
const rightChartRef = ref(null)
const bottomChartRef = ref(null)
const radarChartRef = ref(null)

let leftChart = null
let rightChart = null
let bottomChart = null
let radarChart = null

const currentTime = ref('')
const metrics = ref([])
const leftChartTitle = ref('数据分析')
const rightChartTitle = ref('占比分布')
const bottomChartTitle = ref('趋势分析')
const tableData = ref([])
const mainNumber = ref(0)
const progressItems = ref([])
const topDataList = ref([])

// 更新时间
const updateTime = () => {
  const now = new Date()
  currentTime.value = now.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

// 分析数据并生成看板
/**
 * 数据分析入口：识别数值/分类/日期列 → 生成指标卡、进度项与排行 → 初始化各图表。
 * 约束：列类型识别错误会直接影响图表选择，故对空值与字符串数字做了兼容判断。
 */
const analyzeData = () => {
  console.log('DataAnalysisDashboard - 分析数据:', {
    dataLength: props.data?.length,
    columns: props.columns?.map(c => ({ name: c.columnName, type: c.dataType })),
    inferredColumns: inferredColumns.value?.map(c => ({ name: c.columnName, type: c.dataType }))
  })
  
  if (!props.data || props.data.length === 0) {
    tableData.value = []
    mainNumber.value = 0
    metrics.value = []
    progressItems.value = []
    topDataList.value = []
    ;[leftChart, rightChart, bottomChart, radarChart].forEach(chart => chart?.clear())
    return
  }

  tableData.value = props.data.slice(0, 50)
  mainNumber.value = props.data.length

  // 分析列类型
  const columns = inferredColumns.value
  const numericColumns = columns.filter(col => isNumericColumn(col))
  const categoryColumns = columns.filter(col => isCategoryColumn(col))
  const dateColumns = columns.filter(col => isDateColumn(col))

  console.log('列类型分析:', {
    numeric: numericColumns.map(c => c.columnName),
    category: categoryColumns.map(c => c.columnName),
    date: dateColumns.map(c => c.columnName)
  })

  // 生成指标卡片
  generateMetrics(numericColumns)

  // 生成进度条数据
  generateProgressItems(numericColumns)

  // 生成TOP数据列表
  generateTopDataList(categoryColumns, numericColumns)

  // 生成图表
  nextTick(() => {
    console.log('初始化图表，DOM元素:', {
      left: leftChartRef.value,
      right: rightChartRef.value,
      bottom: bottomChartRef.value,
      radar: radarChartRef.value
    })
    initCharts(numericColumns, categoryColumns, dateColumns)
  })
}

// 判断是否为数值列
const isNumericColumn = (col) => {
  const numericTypes = ['int', 'bigint', 'decimal', 'float', 'double', 'number', 'numeric', 'real', 'long']
  const dataType = (col.dataType || '').toLowerCase()
  return numericTypes.some(type => dataType.includes(type))
}

// 判断是否分类列
const isCategoryColumn = (col) => {
  const categoryTypes = ['varchar', 'char', 'text', 'enum', 'string']
  const dataType = (col.dataType || '').toLowerCase()
  return categoryTypes.some(type => dataType.includes(type))
}

// 判断是否为日期列
const isDateColumn = (col) => {
  const dateTypes = ['date', 'datetime', 'timestamp', 'time']
  const dataType = (col.dataType || '').toLowerCase()
  return dateTypes.some(type => dataType.includes(type))
}

// 生成指标卡片
const generateMetrics = (numericColumns) => {
  metrics.value = [
    { icon: '📈', value: formatNumber(props.data.length), label: '返回记录数' },
    { icon: '▦', value: inferredColumns.value.length, label: '返回字段数' },
  ]
  numericColumns.slice(0, 2).forEach((col) => {
    const mean = props.statistics.columns?.[col.columnName]?.mean
    metrics.value.push({ icon: '∑', value: mean == null ? '未计算' : formatNumber(mean),
      title: mean == null ? '未计算' : decimalValueTitle(mean), label: `${col.comment || col.columnName}平均` })
  })
}

// 生成进度条数据
const generateProgressItems = (numericColumns) => {
  progressItems.value = inferredColumns.value.slice(0, 4).map(col => {
    const present = props.data.filter(row => row[col.columnName] != null && row[col.columnName] !== '').length
    return { label: (col.comment || col.columnName) + '非空率', value: Math.round(10000 * present / props.data.length) / 100, color: 'linear-gradient(90deg, #00d4ff, #0099ff)' }
  })
}

// 生成TOP数据列表
const generateTopDataList = (categoryColumns, numericColumns) => {
  topDataList.value = []
  if (categoryColumns.length > 0) {
    const categoryCol = categoryColumns[0]
    const grouped = Object.create(null)
    props.data.forEach(row => {
      const key = row[categoryCol.columnName] ?? '空值'
      grouped[key] = (grouped[key] || 0) + 1
    })

    const sorted = Object.entries(grouped)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 10)

    const maxValue = sorted[0]?.value || 1
    topDataList.value = sorted.map(item => ({
      ...item,
      percent: (item.value / maxValue) * 100
    }))
  }
}

// 初始化图表
/** 按可用列初始化全部图表（含左下/右下/底部与雷达图）；容器不存在时跳过，避免 ECharts 报错。 */
const initCharts = (numericColumns, categoryColumns, dateColumns) => {
  // 销毁旧图表
  leftChart?.dispose()
  rightChart?.dispose()
  bottomChart?.dispose()
  radarChart?.dispose()

  // 左侧柱状图
  if (leftChartRef.value) {
    leftChart = echarts.init(leftChartRef.value)
    initLeftChart(numericColumns, categoryColumns)
  }

  // 右侧环形图
  if (rightChartRef.value) {
    rightChart = echarts.init(rightChartRef.value)
    initRightChart(categoryColumns)
  }

  // 底部折线图
  if (bottomChartRef.value) {
    bottomChart = echarts.init(bottomChartRef.value)
    initBottomChart(numericColumns, dateColumns)
  }

  // 雷达图
  if (radarChartRef.value) {
    radarChart = echarts.init(radarChartRef.value)
    initRadarChart(numericColumns)
  }
}

// 左侧柱状图
const initLeftChart = (numericColumns, categoryColumns) => {
  console.log('initLeftChart:', { categoryColumns: categoryColumns.length, numericColumns: numericColumns.length })
  
  if (categoryColumns.length > 0 && numericColumns.length > 0) {
    const categoryCol = categoryColumns[0]
    const numericCol = numericColumns[0]
    
    const grouped = Object.create(null)
    props.data.forEach(row => {
      const key = row[categoryCol.columnName] ?? '空值'
      const value = parseFloat(row[numericCol.columnName]) || 0
      grouped[key] = (grouped[key] || 0) + value
    })

    const categories = Object.keys(grouped).slice(0, 8)
    const values = categories.map(k => grouped[k])

    leftChartTitle.value = `${numericCol.comment || numericCol.columnName}按${categoryCol.comment || categoryCol.columnName}合计（前 8 类）`

    const option = {
      backgroundColor: 'transparent',
      tooltip: { 
        trigger: 'axis',
        backgroundColor: 'rgba(0,0,0,0.8)',
        borderColor: '#00d4ff',
        textStyle: { color: '#fff' }
      },
      grid: { left: '3%', right: '4%', bottom: '3%', top: '10%', containLabel: true },
      xAxis: { 
        type: 'category', 
        data: categories,
        axisLabel: { color: '#fff', rotate: 30 },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.3)' } }
      },
      yAxis: { 
        type: 'value',
        axisLabel: { color: '#fff' },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.3)' } },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
      },
      series: [{
        data: values,
        type: 'bar',
        barWidth: '60%',
        itemStyle: {
          borderRadius: [8, 8, 0, 0],
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#00d4ff' },
            { offset: 1, color: '#0099ff' }
          ])
        }
      }]
    }
    leftChart.setOption(safeChartOption(option))
  }
}

// 右侧环形图
const initRightChart = (categoryColumns) => {
  if (categoryColumns.length > 0) {
    const categoryCol = categoryColumns[0]
    const grouped = Object.create(null)
    props.data.forEach(row => {
      const key = row[categoryCol.columnName] ?? '空值'
      grouped[key] = (grouped[key] || 0) + 1
    })

    const ranked = Object.entries(grouped)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
    const data = ranked.slice(0, 6)
    if (ranked.length > 6) data.push({ name: '其余分类合计', value: ranked.slice(6).reduce((total, item) => total + item.value, 0) })

    rightChartTitle.value = `${categoryCol.comment || categoryCol.columnName}占比`

    const option = {
      backgroundColor: 'transparent',
      tooltip: { 
        trigger: 'item',
        backgroundColor: 'rgba(0,0,0,0.8)',
        borderColor: '#00d4ff',
        textStyle: { color: '#fff' }
      },
      legend: { 
        orient: 'vertical', 
        right: 10, 
        top: 'center',
        textStyle: { color: '#fff' }
      },
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        center: ['35%', '50%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 10,
          borderColor: '#1a1f3c',
          borderWidth: 2
        },
        label: { 
          show: true,
          color: '#fff',
          formatter: '{b}\n{d}%'
        },
        emphasis: {
          label: { show: true, fontSize: 14, fontWeight: 'bold' }
        },
        data: data,
        color: ['#00d4ff', '#00ff88', '#ff6b6b', '#ffd93d', '#a855f7', '#f472b6']
      }]
    }
    rightChart.setOption(safeChartOption(option))
  }
}

// 底部折线图
const initBottomChart = (numericColumns, dateColumns) => {
  if (numericColumns.length > 0) {
    const numericCol = numericColumns[0]
    const values = props.data.slice(0, 20).map(row => row[numericCol.columnName] == null ? null : Number(row[numericCol.columnName]))
    const labels = props.data.slice(0, 20).map((_, i) => `第${i + 1}条`)

    bottomChartTitle.value = `${numericCol.comment || numericCol.columnName}按返回顺序（非时间趋势）`

    const option = {
      backgroundColor: 'transparent',
      tooltip: { 
        trigger: 'axis',
        backgroundColor: 'rgba(0,0,0,0.8)',
        borderColor: '#00d4ff',
        textStyle: { color: '#fff' }
      },
      grid: { left: '3%', right: '4%', bottom: '3%', top: '10%', containLabel: true },
      xAxis: { 
        type: 'category', 
        data: labels,
        axisLabel: { color: '#fff' },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.3)' } }
      },
      yAxis: { 
        type: 'value',
        axisLabel: { color: '#fff' },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.3)' } },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
      },
      series: [{
        data: values,
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 8,
        lineStyle: {
          color: '#00ff88',
          width: 3
        },
        itemStyle: {
          color: '#00ff88',
          borderColor: '#fff',
          borderWidth: 2
        },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(0, 255, 136, 0.4)' },
            { offset: 1, color: 'rgba(0, 255, 136, 0.05)' }
          ])
        }
      }]
    }
    bottomChart.setOption(safeChartOption(option))
  }
}

// 雷达图
const initRadarChart = (numericColumns) => {
  const selected = numericColumns.slice(0, 6).filter(col => props.statistics.columns?.[col.columnName]?.mean != null)
  const indicators = selected.map(col => {
    const stats = props.statistics.columns[col.columnName]
    const min = Math.min(0, Number(stats.min))
    const max = Math.max(0, Number(stats.max))
    return { name: col.comment || col.columnName, min, max: max === min ? min + 1 : max }
  })

  if (indicators.length > 0) {
    const values = selected.map(col => Number(props.statistics.columns[col.columnName].mean))

    const option = {
      backgroundColor: 'transparent',
      tooltip: {
        backgroundColor: 'rgba(0,0,0,0.8)',
        borderColor: '#00d4ff',
        textStyle: { color: '#fff' }
      },
      radar: {
        indicator: indicators,
        center: ['50%', '55%'],
        radius: '65%',
        axisName: { color: '#fff' },
        splitArea: {
          areaStyle: {
            color: ['rgba(0, 212, 255, 0.05)', 'rgba(0, 212, 255, 0.1)']
          }
        },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.3)' } },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.2)' } }
      },
      series: [{
        type: 'radar',
        data: [{
          value: values,
          name: '数据指标',
          areaStyle: {
            color: 'rgba(0, 212, 255, 0.3)'
          },
          lineStyle: {
            color: '#00d4ff',
            width: 2
          },
          itemStyle: {
            color: '#00d4ff'
          }
        }]
      }]
    }
    radarChart.setOption(safeChartOption(option))
  }
}

// 刷新图表
const refreshChart = (type) => {
  analyzeData()
}

// 监听数据变化
watch(() => props.data, () => {
  analyzeData()
}, { immediate: true })

// 窗口大小变化时重新调整图表
const handleResize = () => {
  leftChart?.resize()
  rightChart?.resize()
  bottomChart?.resize()
  radarChart?.resize()
}

let timeInterval = null

onMounted(() => {
  window.addEventListener('resize', handleResize)
  updateTime()
  timeInterval = setInterval(updateTime, 1000)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  clearInterval(timeInterval)
  leftChart?.dispose()
  rightChart?.dispose()
  bottomChart?.dispose()
  radarChart?.dispose()
})
</script>

<style scoped>
.data-analysis-dashboard {
  background: linear-gradient(135deg, #0a0e27 0%, #1a1f3c 50%, #0d1225 100%);
  border-radius: 16px;
  padding: 20px;
  color: #fff;
  min-height: 800px;
}

/* 顶部标题栏 */
.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 15px 20px;
  background: linear-gradient(90deg, rgba(0, 212, 255, 0.1), rgba(0, 153, 255, 0.1));
  border-radius: 12px;
  margin-bottom: 20px;
  border: 1px solid rgba(0, 212, 255, 0.3);
}

.header-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-icon {
  font-size: 28px;
}

.title-text {
  font-size: 24px;
  font-weight: bold;
  background: linear-gradient(90deg, #00d4ff, #00ff88);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.header-time {
  font-size: 16px;
  color: #00d4ff;
  font-family: 'Courier New', monospace;
}

/* 指标卡片区域 */
.metrics-section {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
  margin-bottom: 20px;
}

.metric-card {
  background: linear-gradient(135deg, rgba(0, 212, 255, 0.1), rgba(0, 153, 255, 0.05));
  border-radius: 12px;
  padding: 20px;
  border: 1px solid rgba(0, 212, 255, 0.2);
  position: relative;
  overflow: hidden;
  transition: all 0.3s ease;
}

.metric-card:hover {
  transform: translateY(-5px);
  box-shadow: 0 10px 30px rgba(0, 212, 255, 0.3);
}

.metric-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #00d4ff, #00ff88);
}

.metric-icon {
  font-size: 32px;
  margin-bottom: 10px;
}

.metric-content {
  text-align: center;
}

.metric-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 32px;
  font-weight: bold;
  color: #00d4ff;
  margin-bottom: 5px;
  text-shadow: 0 0 20px rgba(0, 212, 255, 0.5);
}

.metric-label {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.7);
  margin-bottom: 8px;
}

.metric-trend {
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 4px;
}

.metric-trend.up {
  color: #00ff88;
  background: rgba(0, 255, 136, 0.2);
}

.metric-trend.down {
  color: #ff6b6b;
  background: rgba(255, 107, 107, 0.2);
}

.trend-arrow {
  font-size: 10px;
}

.metric-progress {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: rgba(255, 255, 255, 0.1);
}

.progress-bar {
  height: 100%;
  background: linear-gradient(90deg, #00d4ff, #00ff88);
  transition: width 1s ease;
}

/* 主要内容区域 */
.main-content {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 20px;
  margin-bottom: 20px;
}

.left-section,
.center-section,
.right-section {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 图表盒子 */
.chart-box {
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0.02));
  border-radius: 12px;
  padding: 15px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  flex: 1;
}

.chart-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.chart-title {
  font-size: 16px;
  font-weight: 600;
  color: #fff;
  display: flex;
  align-items: center;
  gap: 8px;
}

.chart-title::before {
  content: '';
  width: 4px;
  height: 16px;
  background: linear-gradient(180deg, #00d4ff, #00ff88);
  border-radius: 2px;
}

.chart-actions {
  display: flex;
  gap: 10px;
}

.action-btn {
  cursor: pointer;
  padding: 5px;
  border-radius: 4px;
  transition: all 0.3s;
}

.action-btn:hover {
  background: rgba(255, 255, 255, 0.1);
}

.chart-container {
  height: 250px;
}

/* 中间区域特殊样式 */
.center-top {
  text-align: center;
  padding: 20px;
  background: linear-gradient(135deg, rgba(0, 212, 255, 0.1), rgba(0, 255, 136, 0.1));
  border-radius: 12px;
  border: 1px solid rgba(0, 212, 255, 0.3);
}

.big-number-box {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.big-number {
  font-size: 64px;
  font-weight: bold;
  background: linear-gradient(90deg, #00d4ff, #00ff88);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  text-shadow: 0 0 30px rgba(0, 212, 255, 0.5);
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.05); }
}

.big-number-label {
  font-size: 16px;
  color: rgba(255, 255, 255, 0.7);
  margin-top: 10px;
}

.center-chart {
  flex: 2;
}

/* 进度条区域 */
.progress-section {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.progress-item {
  display: flex;
  align-items: center;
  gap: 10px;
}

.progress-label {
  width: 70px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.8);
}

.progress-bar-container {
  flex: 1;
  height: 8px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 4px;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  border-radius: 4px;
  transition: width 1s ease;
}

.progress-value {
  width: 40px;
  text-align: right;
  font-size: 12px;
  color: #00d4ff;
  font-weight: 600;
}

/* 数据列表 */
.data-list-box {
  max-height: 350px;
  overflow: hidden;
}

.data-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.data-list-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 6px;
  transition: all 0.3s;
}

.data-list-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.item-rank {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 12px;
  font-weight: bold;
}

.rank-1 { background: linear-gradient(135deg, #ffd700, #ffaa00); color: #000; }
.rank-2 { background: linear-gradient(135deg, #c0c0c0, #a0a0a0); color: #000; }
.rank-3 { background: linear-gradient(135deg, #cd7f32, #b87333); color: #fff; }
.item-rank:not(.rank-1):not(.rank-2):not(.rank-3) {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

.item-name {
  flex: 1;
  font-size: 13px;
  color: #fff;
}

.item-value {
  width: 50px;
  text-align: right;
  font-size: 13px;
  color: #00d4ff;
  font-weight: 600;
}

.item-bar {
  width: 60px;
  height: 4px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  overflow: hidden;
}

.item-bar-inner {
  height: 100%;
  background: linear-gradient(90deg, #00d4ff, #00ff88);
  border-radius: 2px;
  transition: width 0.5s ease;
}

/* 表格区域 */
.table-section {
  margin-top: 20px;
}

.table-pagination {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.6);
}

/* 响应式 */
@media (max-width: 1200px) {
  .main-content {
    grid-template-columns: 1fr 1fr;
  }
  .right-section {
    grid-column: 1 / -1;
    flex-direction: row;
  }
}

@media (max-width: 768px) {
  .metrics-section {
    grid-template-columns: repeat(2, 1fr);
  }
  .main-content {
    grid-template-columns: 1fr;
  }
}
</style>
