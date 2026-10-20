/**
 * widgetFactory —— 大屏组件实例工厂：按组件类型生成 config 与画布 widget 对象。
 *
 * 使用场景：JimuComponentLibrary（缩略预览）、JimuBigScreenReplica（加入画布）、
 * AI 生成结果落图时都通过这里创建组件，保证三处结构一致。
 * 依赖：`data/jimuScreenPage.json`（同类型组件的种子配置）、`utils/widgetSizing.resolveWidgetSize`（默认尺寸）。
 * 关键约束：
 * - config 必须同时写 chartData（字符串）与 chartDataParsed（结构），历史数据两种形态都要兼容；
 * - createCanvasWidget 与 createPreviewWidget 语义不同：预览可带示例数据用于展示，
 *   而用户新建的组件必须"未绑定"（清空 sql/datasourceId/url/headers/body/drilldown/queryId、
 *   示例值也一并剥离、dataStatus 置为 unbound），否则会把演示订单、金额或数据源凭据带进用户报表；
 * - modelKey 由 component + config 做 FNV-1a 哈希生成，用于识别"同配置组件"，
 *   因此 config 的键顺序会影响哈希值，调整默认配置字段需留意。
 */
import pageSeed from '@/data/jimuScreenPage.json'
import { resolveWidgetSize } from '@/utils/widgetSizing'

const DEFAULT_CHART_DATA = [
  { name: '\u4e00\u6708', value: 36 },
  { name: '\u4e8c\u6708', value: 58 },
  { name: '\u4e09\u6708', value: 42 },
  { name: '\u56db\u6708', value: 76 },
  { name: '\u4e94\u6708', value: 64 }
]

const DEFAULT_TABLE_ROWS = [
  ['14:32:18', 'DD20260716001', 'Mate70 Pro', '\u00a56,299'],
  ['14:31:55', 'DD20260716002', 'V15', '\u00a54,590'],
  ['14:31:22', 'DD20260716003', '\u7cbe\u9009\u793c\u76d2', '\u00a51,280'],
  ['14:30:48', 'DD20260716004', '\u575a\u679c\u793c\u76d2', '\u00a5168']
]

const PREVIEW_METRICS = [
  { name: '经营收入', value: 862, total: 1000, unit: '万', color: '#38bdf8' },
  { name: '项目进度', value: 76, total: 100, unit: '%', color: '#22d3a6' },
  { name: '待处理', value: 18, total: 60, unit: '项', color: '#f0b65b' },
  { name: '质量得分', value: 96.4, total: 100, unit: '分', color: '#8b7cf6' },
]

const PREVIEW_NAVIGATION = [
  { name: '经营分析', icon: 'fa-solid fa-chart-line', color: '#38bdf8' },
  { name: '项目中心', icon: 'fa-solid fa-diagram-project', color: '#8b7cf6' },
  { name: '质量检查', icon: 'fa-solid fa-shield-halved', color: '#22d3a6' },
  { name: '设备预警', icon: 'fa-solid fa-bolt', color: '#f0b65b' },
  { name: '知识中心', icon: 'fa-solid fa-book-open', color: '#54a9ff' },
  { name: '流程中心', icon: 'fa-solid fa-code-branch', color: '#ec6cae' },
]

const PREVIEW_PROJECTS = [
  { name: '智能工厂一期', group: '重点项目', description: '设备接入与质量闭环', time: '09-18', color: '#38bdf8' },
  { name: '华北销售协同', group: '经营项目', description: '订单、回款与库存联动', time: '09-12', color: '#22d3a6' },
  { name: '知识资产治理', group: '平台项目', description: '文档解析与图谱建设', time: '09-06', color: '#8b7cf6' },
]

const PREVIEW_TASKS = [
  { name: '复核设备异常', description: '产线 A · 温度偏高', time: '10:30', icon: 'fa-solid fa-triangle-exclamation' },
  { name: '确认质检报告', description: '批次 QC-2026-0903', time: '今天', icon: 'fa-solid fa-clipboard-check' },
  { name: '更新销售预测', description: '华北区域经营计划', time: '明天', icon: 'fa-solid fa-chart-line' },
]

const PREVIEW_ACTIVITIES = [
  { name: '生产部', description: '完成设备点检并提交记录', time: '2 分钟前', color: '#38bdf8' },
  { name: '质量部', description: '创建外观缺陷复核任务', time: '18 分钟前', color: '#22d3a6' },
  { name: '财务部', description: '更新本月回款数据', time: '1 小时前', color: '#8b7cf6' },
  { name: '系统', description: '数据源健康检查完成', time: '2 小时前', color: '#f0b65b' },
]

const PREVIEW_RANKING = [
  { name: '华北区域', value: 96, color: '#38bdf8' },
  { name: '华东区域', value: 82, color: '#22d3a6' },
  { name: '华南区域', value: 71, color: '#8b7cf6' },
  { name: '西南区域', value: 58, color: '#f0b65b' },
  { name: '东北区域', value: 43, color: '#ec6cae' },
]

const PREVIEW_WEATHER = [
  { day: '今天', weather: '晴', temp: '28°', icon: 'fa-solid fa-sun' },
  { day: '明天', weather: '多云', temp: '26°', icon: 'fa-solid fa-cloud-sun' },
  { day: '周六', weather: '小雨', temp: '23°', icon: 'fa-solid fa-cloud-rain' },
  { day: '周日', weather: '晴', temp: '27°', icon: 'fa-solid fa-sun' },
]

const PREVIEW_HISTORY = [
  { year: 'Q1', name: '方案设计', description: '统一业务口径与数据模型' },
  { year: 'Q2', name: '平台建设', description: '完成数据、流程和权限底座' },
  { year: 'Q3', name: '场景上线', description: '报表、预警和质检投入使用' },
  { year: 'Q4', name: '持续优化', description: '基于运行数据迭代模型' },
]

const PREVIEW_FORM_FIELDS = [
  { label: '业务主题', value: '请选择' },
  { label: '负责人', value: '请输入' },
  { label: '所属部门', value: '全部' },
]

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function widgetSize(item) {
  return resolveWidgetSize(item)
}

function modelKey(component, config) {
  const source = `${component}:${JSON.stringify(config)}`
  let hash = 2166136261
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return `${component}-${(hash >>> 0).toString(16)}`
}

function previewConfig(data = [], option = {}) {
  return {
    dataType: 1,
    dataStatus: 'preview',
    background: '#FFFFFF00',
    borderColor: '#FFFFFF00',
    chartData: JSON.stringify(data),
    chartDataParsed: clone(data),
    option,
  }
}

/** 预览示例数据按组件类型分派；返回 null 表示该类型没有专门示例，交给后续通用分支处理。 */
function businessPreviewConfig(item) {
  const type = String(item.compType || '')
  const name = String(item.name || '')
  if (['JCalendar', 'JMultiViewCalendar', 'JPermanentCalendar'].includes(type)) {
    const today = new Date()
    const month = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
    return previewConfig([
      { name: '经营复盘', date: `${month}-08`, color: '#38bdf8' },
      { name: '质量周会', date: `${month}-18`, color: '#22d3a6' },
    ], { calendar: { month } })
  }
  if (type === 'JFilterQuery') return previewConfig([], {
    fields: [
      { name: 'keyword', label: '关键词', type: 'text' },
      { name: 'status', label: '状态', type: 'select' },
      { name: 'date', label: '日期范围', type: 'date' },
    ],
  })
  if (type === 'JCustomButton') return previewConfig([{ name: name || '立即执行' }])
  if (type === 'JTabs') return previewConfig([{ name: '概览' }, { name: '明细' }, { name: '趋势' }])
  if (type === 'JGrid') return previewConfig([])
  if (['JGrowCard', 'JSimpleCard', 'JColorBlock'].includes(type)) return previewConfig(PREVIEW_METRICS)
  if (type === 'JQuickNav') return previewConfig(PREVIEW_NAVIGATION)
  if (type === 'JProjectCard') return previewConfig(PREVIEW_PROJECTS)
  if (type === 'JWaitMatter') return previewConfig(PREVIEW_TASKS)
  if (type === 'JDynamicInfo') return previewConfig(PREVIEW_ACTIVITIES)
  if (['JFlashList', 'JRankingList', 'JFlashCloud'].includes(type)) return previewConfig(PREVIEW_RANKING)
  if (['JList', 'JScrollList'].includes(type)) return previewConfig(PREVIEW_TASKS)
  if (type === 'JDevHistory') return previewConfig(PREVIEW_HISTORY)
  if (type === 'JWeatherForecast') return previewConfig(PREVIEW_WEATHER)
  if (['JCardCarousel', 'JCardScroll', 'JCarousel'].includes(type)) return previewConfig(PREVIEW_PROJECTS)
  if (['JForm', 'online', 'design'].includes(type)) return previewConfig(PREVIEW_FORM_FIELDS)
  if (type === 'JGender') return previewConfig([{ name: '女性', value: 54 }, { name: '男性', value: 46 }])
  if (type === 'JOrbitRing') return previewConfig(PREVIEW_NAVIGATION)
  if (type === 'JCommon') {
    if (/甘特/.test(name)) return previewConfig(PREVIEW_HISTORY.map((item, index) => ({ ...item, name: item.name, startPercent: index * 15, progress: 28 + index * 7 })))
    return previewConfig(PREVIEW_METRICS)
  }
  return null
}

function hifiWidgetConfig(item) {
  const variant = item.panelVariant || ''
  if (variant === 'hifi-title-center-wing') {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: { value: '大数据智能可视化协同指挥平台' },
      chartDataParsed: { value: '大数据智能可视化协同指挥平台' },
      option: {
        body: {
          text: '大数据智能可视化协同指挥平台',
          color: '#00f2fe',
          fontSize: 24,
          fontWeight: 'bold',
          textAlign: 'center'
        }
      }
    }
  }
  if (variant === 'hifi-title-left-bracket') {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: { value: '销售行业数据可视化模板' },
      chartDataParsed: { value: '销售行业数据可视化模板' },
      option: {
        body: {
          text: '销售行业数据可视化模板',
          color: '#00f2fe',
          fontSize: 22,
          fontWeight: 'bold',
          textAlign: 'left'
        }
      }
    }
  }
  if (variant === 'hifi-section-pill') {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: { value: '销售数据总揽 /Sales Data Overview' },
      chartDataParsed: { value: '销售数据总揽 /Sales Data Overview' },
      option: {
        body: {
          text: '销售数据总揽 /Sales Data Overview',
          color: '#ffffff',
          fontSize: 14,
          fontWeight: 'bold',
          textAlign: 'left'
        }
      }
    }
  }
  if (variant === 'hifi-time-capsule') {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: { value: '2026-10-12 11:20:00 星期一' },
      chartDataParsed: { value: '2026-10-12 11:20:00 星期一' },
      option: {
        body: {
          text: '2026-10-12 11:20:00 星期一',
          color: '#00f2fe',
          fontSize: 13,
          textAlign: 'center'
        }
      }
    }
  }
  if (variant === 'hifi-frame-chamfer') {
    return {
      dataType: 1,
      background: 'rgba(5, 18, 44, 0.85)',
      borderColor: '#00f2fe',
      chartData: [],
      chartDataParsed: [],
      option: { type: '4', mainColor: '#00f2fe', subColor: '#0066ff', title: item.name }
    }
  }
  if (variant === 'hifi-frame-bracket') {
    return {
      dataType: 1,
      background: 'rgba(4, 15, 36, 0.78)',
      borderColor: '#00f2fe',
      chartData: [],
      chartDataParsed: [],
      option: { type: '1', mainColor: '#00f2fe', subColor: '#0055dd', title: item.name }
    }
  }
  if (variant === 'hifi-frame-glow-box') {
    return {
      dataType: 1,
      background: 'rgba(8, 28, 64, 0.75)',
      borderColor: 'rgba(0, 242, 254, 0.5)',
      chartData: [],
      chartDataParsed: [],
      option: { type: '12', mainColor: '#00f2fe', subColor: '#0044bb', title: item.name }
    }
  }
  if (variant === 'hifi-metric-hexagon') {
    const data = [{ name: '当月签约额', value: 1323.12, unit: '万元' }]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        card: {
          showIcon: true,
          icon: 'fa-solid fa-yen-sign',
          titleFontSize: 11,
          valueFontSize: 20,
          fill: { color: 'rgba(6, 22, 52, 0.7)' },
          borderColor: 'rgba(0, 242, 254, 0.3)'
        }
      }
    }
  }
  if (variant === 'hifi-metric-circle-kpi') {
    const data = [{ name: '当月回款额', value: 678.13, unit: '万元' }]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        card: {
          showIcon: true,
          icon: 'fa-solid fa-coins',
          titleFontSize: 11,
          valueFontSize: 20,
          fill: { color: 'rgba(5, 18, 42, 0.7)' },
          borderColor: 'rgba(34, 211, 166, 0.3)'
        }
      }
    }
  }
  if (variant === 'hifi-metric-neon-counter') {
    const data = [{ name: '本月计划完成率', value: 96.8, unit: '%' }]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        card: {
          titleFontSize: 13,
          valueFontSize: 44,
          color: '#00f2fe'
        }
      }
    }
  }
  if (variant === 'hifi-metric-dual-compare') {
    const data = [
      { name: '本月平均时长', value: 44.0, unit: 'min' },
      { name: '上月平均时长', value: 42.0, unit: 'min' }
    ]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        card: {
          titleFontSize: 11,
          valueFontSize: 18
        }
      }
    }
  }
  if (variant === 'hifi-chart-gradient-bar') {
    const data = [
      { name: '1月', value: 320 },
      { name: '2月', value: 450 },
      { name: '3月', value: 580 },
      { name: '4月', value: 420 },
      { name: '5月', value: 680 },
      { name: '6月', value: 750 }
    ]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        color: ['#00f2fe', '#0072ff'],
        xAxis: { axisLabel: { color: '#88a8cc' } },
        yAxis: { axisLabel: { color: '#88a8cc' }, splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.06)' } } },
        series: [{ barWidth: 14, itemStyle: { borderRadius: [4, 4, 0, 0] } }]
      }
    }
  }
  if (variant === 'hifi-chart-dual-bar') {
    const data = [
      { name: '华东', '签约额': 580, '回款额': 420 },
      { name: '华南', '签约额': 450, '回款额': 360 },
      { name: '华北', '签约额': 490, '回款额': 390 },
      { name: '华中', '签约额': 320, '回款额': 280 },
      { name: '西北', '签约额': 260, '回款额': 210 }
    ]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        color: ['#00f2fe', '#ffd200'],
        xAxis: { axisLabel: { color: '#88a8cc' } },
        yAxis: { axisLabel: { color: '#88a8cc' }, splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.06)' } } }
      }
    }
  }
  if (variant === 'hifi-chart-smooth-area') {
    const data = [
      { name: '1月', value: 1200 },
      { name: '2月', value: 2400 },
      { name: '3月', value: 1800 },
      { name: '4月', value: 3200 },
      { name: '5月', value: 2800 },
      { name: '6月', value: 4100 }
    ]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        color: ['#00f2fe'],
        xAxis: { axisLabel: { color: '#88a8cc' } },
        yAxis: { axisLabel: { color: '#88a8cc' }, splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.06)' } } },
        series: [{ smooth: true, areaStyle: { opacity: 0.35 } }]
      }
    }
  }
  if (variant === 'hifi-chart-donut-percent') {
    const data = [
      { name: '电子产品', value: 33.2 },
      { name: '服装配饰', value: 23.4 },
      { name: '日用百货', value: 18.4 },
      { name: '家电数码', value: 15.4 },
      { name: '家居建材', value: 9.6 }
    ]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        color: ['#00f2fe', '#38ef7d', '#ffd200', '#ff6a00', '#8b5cf6'],
        series: [{ radius: ['44%', '68%'] }]
      }
    }
  }
  if (variant === 'hifi-chart-capsule-rank') {
    const data = [
      { name: '华东大区', value: 98 },
      { name: '华南大区', value: 92 },
      { name: '华北大区', value: 85 },
      { name: '华中大区', value: 78 },
      { name: '西北大区', value: 65 }
    ]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        color: ['#00f2fe']
      }
    }
  }
  if (variant === 'hifi-ranking-pill-top5') {
    const data = [
      { rank: 1, name: '华东大区', value: 6845 },
      { rank: 2, name: '华南大区', value: 5375 },
      { rank: 3, name: '华北大区', value: 4560 },
      { rank: 4, name: '华中大区', value: 3344 },
      { rank: 5, name: '西北大区', value: 2330 }
    ]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        color: ['#00f2fe']
      }
    }
  }
  if (variant === 'hifi-table-tech-board') {
    const data = [
      ['销售1部', '400000', '368000', '92.0%'],
      ['销售2部', '350000', '315000', '90.0%'],
      ['销售3部', '300000', '285000', '95.0%'],
      ['销售4部', '280000', '246400', '88.0%']
    ]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(data),
      chartDataParsed: clone(data),
      option: {
        header: [
          { label: '部门' },
          { label: '销售目标' },
          { label: '实际完成' },
          { label: '完成率' }
        ],
        headerBGC: 'rgba(0, 150, 255, 0.25)',
        oddRowBGC: 'rgba(6, 24, 56, 0.5)',
        evenRowBGC: 'rgba(3, 15, 38, 0.3)',
        fontColor: '#c8e2fb'
      }
    }
  }
  return null
}

/**
 * 按组件类型生成默认 config（含示例数据与默认样式）。
 * 判定顺序即优先级：视频/内嵌页/单选/富文本/图片等特殊类型先命中，
 * 再走业务组件示例，最后按组件名正则匹配文本、边框、装饰、表格与通用图表。
 * @param {{ compType?: string, name?: string, imageUrl?: string }} item 组件库条目
 * @returns {Record<string, any>} 组件配置对象（新对象，可安全修改）
 */
export function createWidgetConfig(item) {
  if (item.panelVariant && item.panelVariant.startsWith('hifi-')) {
    const hifiConfig = hifiWidgetConfig(item)
    if (hifiConfig) return hifiConfig
  }

  if (/VideoPlay|VideoJs/i.test(item.compType)) {
    return {
      dataType: 1,
      background: '#071423',
      borderColor: '#FFFFFF00',
      videoUrl: '',
      option: { autoplay: false, controls: true, fit: 'contain' }
    }
  }

  if (/Iframe/i.test(item.compType)) {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      url: '',
      option: { sandbox: true }
    }
  }

  if (/SelectRadio|TabToggle|RadioButton/i.test(item.compType)) {
    const values = [{ label: '选项一', value: '1' }, { label: '选项二', value: '2' }, { label: '选项三', value: '3' }]
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(values),
      chartDataParsed: clone(values),
      option: { activeIndex: 0, activeColor: '#1688df', color: '#c9d8e6' }
    }
  }

  if (/DragEditor/i.test(item.compType)) {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      option: { body: { text: '<p>富文本内容</p>', color: '#d0e0f0', fontSize: 16 } }
    }
  }

  if (item.compType === 'JImg' || (item.imageUrl && /Img|Icon|Carousel/i.test(item.compType))) {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      imageUrl: item.imageUrl,
      option: { fit: 'contain' }
    }
  }

  const businessConfig = businessPreviewConfig(item)
  if (businessConfig) return businessConfig

  if (/Text|Title|CountTo|Number|CurrentTime/i.test(item.compType)) {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: { value: item.name },
      option: {
        body: {
          text: item.name,
          color: '#d0e0f0',
          fontSize: 34,
          fontWeight: 'bold',
          textAlign: 'center'
        }
      }
    }
  }

  if (/Border/i.test(item.compType)) {
    const variant = item.name.match(/(\d+)$/)?.[1] || '7'
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: [],
      option: { type: variant, mainColor: '#05f8d6', subColor: '#0082fc', title: item.name }
    }
  }

  if (/Decoration/i.test(item.compType)) {
    const variant = item.name.match(/(\d+)$/)?.[1] || '5'
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: [],
      option: { type: variant, mainColor: '#05f8d6', subColor: '#0082fc' }
    }
  }

  if (/Table|Board|List/i.test(item.compType)) {
    return {
      dataType: 1,
      background: '#FFFFFF00',
      borderColor: '#FFFFFF00',
      chartData: JSON.stringify(DEFAULT_TABLE_ROWS),
      chartDataParsed: clone(DEFAULT_TABLE_ROWS),
      option: {
        header: [
          { label: '\u65f6\u95f4' },
          { label: '\u8ba2\u5355\u53f7' },
          { label: '\u5546\u54c1\u540d' },
          { label: '\u91d1\u989d' }
        ],
        headerBGC: '#1e3048',
        oddRowBGC: '#1e3048aa',
        evenRowBGC: '#1e304833',
        fontColor: '#c0d4ee'
      }
    }
  }

  const sameType = pageSeed.widgets.find(widget => widget.component === item.compType)
  if (sameType) return clone(sameType.config)

  return {
    dataType: 1,
    background: '#FFFFFF00',
    borderColor: '#FFFFFF00',
    chartData: JSON.stringify(DEFAULT_CHART_DATA),
    chartDataParsed: clone(DEFAULT_CHART_DATA),
    option: {
      color: ['#36d7b7', '#4a9eff', '#f7c948'],
      title: {
        show: true,
        text: item.name,
        textStyle: { color: '#5ba8f0', fontSize: 13, fontWeight: 'normal' }
      },
      xAxis: { axisLabel: { color: '#EEF1FA' } },
      yAxis: { axisLabel: { color: '#EEF1FA' }, splitLine: { show: false } },
      series: [{}]
    }
  }
}

function baseWidget(item, id) {
  // 参考组件沿用模板中的完整皮肤与原始尺寸；预览、拖入画布使用同一实例结构。
  if (item.referenceWidget) return {
    ...clone(item.referenceWidget), id, i: id, x: 0, y: 0, locked: false,
    modelKey: modelKey(item.compType, item.referenceWidget.config || {}),
  }
  const { width, height } = widgetSize(item)
  const config = createWidgetConfig(item)
  return {
    id,
    i: id,
    pageCompId: '',
    visible: true,
    locked: false,
    rotation: 0,
    x: 0,
    y: 0,
    w: width,
    h: height,
    pcX: 0,
    pcY: 0,
    pcW: width,
    orderNum: 1,
    component: item.compType,
    componentName: item.name,
    panelVariant: item.panelVariant || '',
    config,
    modelKey: modelKey(item.compType, config)
  }
}

/**
 * 生成组件库缩略预览用的 widget（id 固定为 preview-<id>，仅用于渲染，不入画布）。
 * 允许带示例数据，因为它只用于说明组件长什么样。
 */
export function createPreviewWidget(item) {
  return baseWidget(item, `preview-${item.id}`)
}

/**
 * 生成加入画布的真实组件：随机本地 id、按舞台居中落位、写入 orderNum。
 * 关键约束：除纯展示类组件（文本/标题/时间/富文本/边框/装饰/图片/图标/视频/内嵌页/单选/色块）外，
 * 必须剥离示例数据与数据绑定（sql/datasourceId/url/apiUrl/headers/body/drilldown/queryId），
 * 并把 dataStatus 置为 unbound —— 用户的新报表不能继承组件库的演示订单、金额或数据源凭据。
 * 参考组件加入业务画布后也从空数据开始；原图示例只在组件库预览中展示。
 * @param {object} item 组件库条目
 * @param {{ stageWidth: number, stageHeight: number, orderNum: number }} options 舞台尺寸与层级
 * @returns {object} 画布 widget 对象
 */
export function createCanvasWidget(item, { stageWidth, stageHeight, orderNum }) {
  const id = `local-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`
  const widget = baseWidget(item, id)
  // 组件库预览可以展示示例，业务画布必须从未绑定状态开始，
  // 不能带入示例订单、金额或数据源凭据。
  if (item.referenceWidget || (!/Text|Title|CurrentTime|DragEditor|Border|Decoration|Img|Image|Icon|Video|Iframe|SelectRadio|TabToggle|RadioButton|ColorBlock/i.test(item.compType)
      || /CountTo|Number|Stats|Status/.test(item.compType))) {
    const config = widget.config
    for (const key of ['sql', 'datasourceId', 'url', 'apiUrl', 'headers', 'body', 'drilldown', 'queryId']) delete config[key]
    config.dataType = 1
    config.dataStatus = 'unbound'
    delete config.referenceSample
    config.chartData = '[]'
    config.chartDataParsed = []
    function stripValues(value) {
      if (Array.isArray(value)) return value.map(stripValues)
      if (!value || typeof value !== 'object') return value
      return Object.fromEntries(Object.entries(value)
        .filter(([key]) => !['data', 'value', 'dataset', 'source', 'nodes', 'links', 'indicator'].includes(key))
        .map(([key, child]) => [key, stripValues(child)]))
    }
    config.option = stripValues(config.option || {})
  }
  widget.x = Math.max(0, Math.round((stageWidth - widget.w) / 2))
  widget.y = Math.max(0, Math.round((stageHeight - widget.h) / 2))
  widget.orderNum = orderNum
  return widget
}
