<template>
  <div class="report-designer">
    <!-- 顶部工具栏 -->
    <header class="designer-topbar">
      <div class="topbar-left">
        <button class="tb-btn ghost" @click="goBack"><i class="fa-solid fa-arrow-left"></i></button>
        <div class="report-title">
          <i class="fa-solid fa-tv"></i>
          <input v-model="report.reportName" class="title-input" placeholder="未命名大屏" />
        </div>
      </div>
      <div class="topbar-center">
        <span class="canvas-size">{{ report.width }} × {{ report.height }}</span>
        <div class="zoom-ctrl">
          <button class="tb-btn ghost sm" @click="zoomOut"><i class="fa-solid fa-minus"></i></button>
          <span class="zoom-val">{{ Math.round(scale * 100) }}%</span>
          <button class="tb-btn ghost sm" @click="zoomIn"><i class="fa-solid fa-plus"></i></button>
          <button class="tb-btn ghost sm" @click="fitScreen" title="适应屏幕"><i class="fa-solid fa-expand"></i></button>
        </div>
      </div>
      <div class="topbar-right">
        <button class="tb-btn" @click="preview"><i class="fa-solid fa-eye"></i> 预览</button>
        <button class="tb-btn primary" :disabled="saving" @click="save">
          <i class="fa-solid fa-floppy-disk"></i> {{ saving ? '保存中' : '保存' }}
        </button>
      </div>
    </header>

    <div class="designer-body">
      <!-- 左侧：组件库 + AI + 图层 -->
      <aside class="designer-left">
        <div class="left-tabs">
          <span :class="['ltab', leftTab==='comp'&&'active']" @click="leftTab='comp'">组件库</span>
          <span :class="['ltab', leftTab==='ai'&&'active']" @click="leftTab='ai'">AI图表</span>
          <span :class="['ltab', leftTab==='layer'&&'active']" @click="leftTab='layer'">图层</span>
        </div>

        <!-- 组件库 -->
        <div v-show="leftTab==='comp'" class="comp-lib">
          <div v-for="cat in componentLib" :key="cat.name" class="comp-cat">
            <div class="comp-cat-title">{{ cat.name }}</div>
            <div class="comp-grid">
              <div
                v-for="c in cat.items"
                :key="c.type + (c.chartType||'') + (c.variant||'')"
                class="comp-item"
                draggable="true"
                @dragstart="onCompDragStart(c)"
                @click="addWidget(c)"
              >
                <i class="fa-solid" :class="c.icon"></i>
                <span>{{ c.label }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- AI 图表 -->
        <div v-show="leftTab==='ai'" class="ai-panel">
          <div class="ai-mode">
            <span :class="['ai-mode-btn', aiMode==='screen'&&'active']" @click="aiMode='screen'"><i class="fa-solid fa-table-cells-large"></i> 整屏生成</span>
            <span :class="['ai-mode-btn', aiMode==='widget'&&'active']" @click="aiMode='widget'"><i class="fa-solid fa-chart-simple"></i> 单个图表</span>
          </div>
          <el-select v-model="aiDatasourceId" placeholder="选择数据源" size="small" class="ds-select" :loading="datasourceLoading" :disabled="datasourceLoading">
            <el-option v-for="ds in datasources" :key="ds.id" :label="ds.name" :value="ds.id" />
          </el-select>
          <div v-if="datasourceError" class="datasource-error" role="alert">
            <span>{{ datasourceError }}</span>
            <button type="button" :disabled="datasourceLoading" @click="loadDatasources">重新读取</button>
          </div>

          <!-- 整屏生成 -->
          <template v-if="aiMode==='screen'">
            <textarea v-model="aiTheme" class="ai-input" placeholder="输入大屏主题，例如：销售分析大屏 / 采购监控大屏 / 客户分布大屏"></textarea>
            <div class="theme-chips">
              <span v-for="t in themeExamples" :key="t" class="theme-chip" @click="aiTheme=t">{{ t }}</span>
            </div>
            <button class="ai-generate-btn" :disabled="aiLoading" @click="generateScreen">
              <i class="fa-solid" :class="aiLoading ? 'fa-spinner fa-spin' : 'fa-wand-magic-sparkles'"></i>
              {{ aiLoading ? 'AI 生成大屏中...' : '一键生成大屏' }}
            </button>
            <p class="ai-hint">AI 规划整屏：标题 + 指标卡 + 多张图表，自动查数并铺满画布</p>
          </template>

          <!-- 单图 -->
          <template v-else>
            <textarea v-model="aiRequirement" class="ai-input" placeholder="用一句话描述想看的图表，例如：各地区客户数量饼图"></textarea>
            <button class="ai-generate-btn" :disabled="aiLoading" @click="generateWidget">
              <i class="fa-solid" :class="aiLoading ? 'fa-spinner fa-spin' : 'fa-bolt'"></i>
              {{ aiLoading ? 'AI 生成中...' : '生成图表' }}
            </button>
            <p class="ai-hint">AI 自动生成 SQL、查询数据并选择图表，添加到大屏</p>
          </template>
        </div>

        <!-- 图层 -->
        <div v-show="leftTab==='layer'" class="layer-list">
          <div v-for="w in widgets" :key="w.id" class="layer-item" :class="{active: activeId===w.id}" @click="selectWidget(w.id)">
            <i class="fa-solid" :class="widgetIcon(w)"></i>
            <span class="layer-name">{{ w.title || w.type }}</span>
            <i class="fa-solid fa-trash layer-del" @click.stop="removeWidget(w.id)"></i>
          </div>
          <div v-if="widgets.length===0" class="layer-empty">暂无组件</div>
        </div>
      </aside>

      <!-- 中间：画布 -->
      <main class="designer-canvas-wrap" ref="canvasWrapRef" @mousedown.self="activeId=null"
            @dragover.prevent @drop="onCanvasDrop">
        <div class="designer-canvas" :style="canvasStyle">
          <div
            v-for="w in widgets" :key="w.id"
            class="widget-box" :class="{active: activeId===w.id}"
            :style="widgetStyle(w)"
            @mousedown.stop="startDrag($event, w)"
          >
            <div class="widget-inner" :style="innerStyle(w)">
              <!-- 图表 -->
              <div v-if="w.type==='chart'" class="w-chart" :ref="el => registerChartEl(w.id, el)">
                <div v-if="w.dataStatus === 'unbound'" class="w-unbound-state">待绑定数据<small>请选择数据源并生成或绑定查询</small></div>
              </div>
              <!-- 指标卡 -->
              <div v-else-if="w.type==='metric'" class="w-metric">
                <div class="w-metric-value" :style="{color: w.style.valueColor}">{{ w.value }}<span class="w-metric-unit">{{ w.unit }}</span></div>
                <div class="w-metric-label">{{ w.dataStatus === 'unbound' ? '待绑定指标' : w.title }}</div>
              </div>
              <!-- 标题/文本 -->
              <div v-else-if="w.type==='title' || w.type==='text'" class="w-text">{{ w.content }}</div>
              <!-- 时间 -->
              <div v-else-if="w.type==='time'" class="w-text">{{ nowTime }}</div>
              <!-- 表格 -->
              <div v-else-if="w.type==='table'" class="w-table">
                <table>
                  <thead><tr><th v-for="(h,i) in tableHeaders(w)" :key="i">{{ h }}</th></tr></thead>
                  <tbody><tr v-for="(row,ri) in tableRows(w)" :key="ri"><td v-for="(h,ci) in tableHeaders(w)" :key="ci">{{ row[h] }}</td></tr></tbody>
                </table>
                <div v-if="w.dataStatus === 'unbound'" class="w-unbound-state">待绑定数据<small>请选择数据源并绑定查询</small></div>
              </div>
              <!-- 边框/装饰 -->
              <div v-else-if="w.type==='border'" class="w-border" :class="'border-'+w.variant"></div>
            </div>
            <span v-if="activeId===w.id" class="resize-handle" @mousedown.stop="startResize($event, w)"></span>
          </div>
          <div v-if="widgets.length===0" class="canvas-empty">从左侧组件库拖入或点击组件开始搭建大屏</div>
        </div>
      </main>

      <!-- 右侧：属性面板 -->
      <aside class="designer-right">
        <div class="panel-title"><i class="fa-solid fa-sliders"></i> {{ activeWidget ? '组件属性' : '大屏设置' }}</div>
        <!-- PLACEHOLDER_PROP_PANEL -->
        <div class="prop-scroll">
          <template v-if="activeWidget">
            <!-- 通用: 位置尺寸 -->
            <div class="prop-sec">
              <div class="prop-sec-title">位置与尺寸</div>
              <div class="prop-row">
                <div class="prop-group half"><label>X</label><input type="number" v-model.number="activeWidget.position.left" class="prop-input" /></div>
                <div class="prop-group half"><label>Y</label><input type="number" v-model.number="activeWidget.position.top" class="prop-input" /></div>
              </div>
              <div class="prop-row">
                <div class="prop-group half"><label>宽</label><input type="number" v-model.number="activeWidget.position.width" class="prop-input" @change="renderChart(activeWidget)" /></div>
                <div class="prop-group half"><label>高</label><input type="number" v-model.number="activeWidget.position.height" class="prop-input" @change="renderChart(activeWidget)" /></div>
              </div>
            </div>

            <!-- 标题/文本 -->
            <div class="prop-sec" v-if="['title','text','metric','chart','table'].includes(activeWidget.type)">
              <div class="prop-sec-title">内容</div>
              <div class="prop-group" v-if="['title','text'].includes(activeWidget.type)">
                <label>文字</label>
                <input v-model="activeWidget.content" class="prop-input" />
              </div>
              <div class="prop-group" v-else>
                <label>标题</label>
                <input v-model="activeWidget.title" class="prop-input" @input="renderChart(activeWidget)" />
              </div>
              <div class="prop-row" v-if="activeWidget.type==='metric'">
                <div class="prop-group half"><label>数值</label><input v-model="activeWidget.value" class="prop-input" /></div>
                <div class="prop-group half"><label>单位</label><input v-model="activeWidget.unit" class="prop-input" /></div>
              </div>
            </div>

            <!-- 样式: 颜色/字体/边框 -->
            <div class="prop-sec">
              <div class="prop-sec-title">样式</div>
              <div class="prop-group">
                <label>背景色</label>
                <div class="color-row">
                  <input type="color" v-model="activeWidget.style.background" class="prop-color" />
                  <input type="range" min="0" max="1" step="0.05" v-model.number="activeWidget.style.bgOpacity" class="prop-range" />
                  <span class="range-val">{{ Math.round((activeWidget.style.bgOpacity??1)*100) }}%</span>
                </div>
              </div>
              <div class="prop-row">
                <div class="prop-group half"><label>边框色</label><input type="color" v-model="activeWidget.style.borderColor" class="prop-color" /></div>
                <div class="prop-group half"><label>边框宽</label><input type="number" min="0" v-model.number="activeWidget.style.borderWidth" class="prop-input" /></div>
              </div>
              <div class="prop-row">
                <div class="prop-group half"><label>圆角</label><input type="number" min="0" v-model.number="activeWidget.style.radius" class="prop-input" /></div>
                <div class="prop-group half" v-if="['title','text','metric','time'].includes(activeWidget.type)"><label>字号</label><input type="number" v-model.number="activeWidget.style.fontSize" class="prop-input" /></div>
              </div>
              <div class="prop-group" v-if="['title','text','metric','time'].includes(activeWidget.type)">
                <label>字体颜色</label>
                <input type="color" v-model="activeWidget.style.color" class="prop-color" />
              </div>
              <div class="prop-group" v-if="activeWidget.type==='metric'">
                <label>数值颜色</label>
                <input type="color" v-model="activeWidget.style.valueColor" class="prop-color" />
              </div>
            </div>

            <!-- 图表配色 -->
            <div class="prop-sec" v-if="activeWidget.type==='chart'">
              <div class="prop-sec-title">图表配色</div>
              <div class="palette-row">
                <span v-for="(p,i) in palettes" :key="i" class="palette" @click="applyPalette(activeWidget, p)">
                  <span v-for="(c,ci) in p.slice(0,5)" :key="ci" :style="{background:c}"></span>
                </span>
              </div>
              <div class="prop-group" v-if="activeWidget.query?.sql">
                <label>SQL（只读）</label>
                <textarea :value="activeWidget.query.sql" class="prop-input sql" readonly rows="4"></textarea>
              </div>
            </div>

            <button class="prop-danger" @click="removeWidget(activeWidget.id)"><i class="fa-solid fa-trash"></i> 删除组件</button>
          </template>

          <!-- 大屏设置 -->
          <template v-else>
            <div class="prop-sec">
              <div class="prop-sec-title">画布</div>
              <div class="prop-row">
                <div class="prop-group half"><label>宽度</label><input type="number" v-model.number="report.width" class="prop-input" /></div>
                <div class="prop-group half"><label>高度</label><input type="number" v-model.number="report.height" class="prop-input" /></div>
              </div>
              <div class="prop-group"><label>背景色</label><input type="color" v-model="report.backgroundColor" class="prop-color" /></div>
              <div class="prop-group"><label>背景图URL</label><input v-model="report.backgroundImage" class="prop-input" placeholder="选填" /></div>
              <div class="prop-group"><label>自动刷新(秒)</label><input type="number" v-model.number="report.refreshSeconds" class="prop-input" /></div>
              <div class="prop-quick">
                <span class="qtheme" v-for="t in canvasThemes" :key="t.name" :style="{background:t.bg}" @click="report.backgroundColor=t.bg" :title="t.name"></span>
              </div>
            </div>
            <p class="prop-tip">点击画布中的组件可编辑其颜色、边框、字体等样式</p>
          </template>
        </div>
      </aside>
    </div>
  </div>
</template>

<!-- PLACEHOLDER_SCRIPT -->
<script setup>
/**
 * 基础报表设计器（路由 /report/basic/:id，权限 dashboards.edit）。
 * 职责：画布尺寸/背景配置、组件拖拽与缩放、组件库选择、ECharts 预览渲染、AI 生成单组件或整屏，以及保存与预览。
 * 依赖接口：api/report.js（getById / create / update，内容以 content JSON 提交）与 /api/data-analysis/datasources（数据源下拉）。
 * 关键交互：组件样式用绝对坐标 + 缩放比例渲染（fitScreen 自适应）；拖动/缩放结束才重绘图表（性能考虑）；
 *   AI 生成走 reportApi.aiGenerate / aiGenerateScreen（超时较长），生成结果插入当前画布。
 * 约束：保存提交的是 widgets 快照（buildContent），画布坐标以 1920×1080 为基准；未保存修改需用户自行确认离开。
 * 错误呈现：加载失败 ElMessage.error('加载报表失败')；保存/生成失败均以 ElMessage 提示，不清空当前画布。
 */
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import reportApi from '@/api/report'
import { get } from '@/api/request'

const route = useRoute()
const router = useRouter()

// 把后端错误收敛为用户可以直接处理的中文提示，避免把空白异常展示给用户。
const readableError = (error, fallback) => String(error?.message || error?.response?.data?.message || fallback).trim() || fallback

const report = reactive({
  id: null, reportName: '未命名大屏', description: '', datasourceId: null,
  width: 1920, height: 1080, backgroundColor: '#0a1834', backgroundImage: '',
  refreshSeconds: 0, enabled: 1
})

const widgets = ref([])
const activeId = ref(null)
const scale = ref(0.35)          // 默认更小，展示整屏
const saving = ref(false)
const leftTab = ref('comp')

const datasources = ref([])
const aiDatasourceId = ref(null)
const datasourceLoading = ref(false)
const datasourceError = ref('')
const aiRequirement = ref('')
const aiLoading = ref(false)
const aiMode = ref('screen')
const aiTheme = ref('')
const themeExamples = ['销售分析大屏', '采购监控大屏', '客户分布大屏']

const canvasWrapRef = ref(null)
const chartEls = {}
const chartInstances = {}
const nowTime = ref('')
let clockTimer = null

// ===== 组件库 =====
const componentLib = [
  { name: '图表', items: [
    { type:'chart', chartType:'bar', label:'柱状图', icon:'fa-chart-column' },
    { type:'chart', chartType:'line', label:'折线图', icon:'fa-chart-line' },
    { type:'chart', chartType:'pie', label:'饼图', icon:'fa-chart-pie' },
    { type:'chart', chartType:'scatter', label:'散点图', icon:'fa-braille' },
    { type:'chart', chartType:'radar', label:'雷达图', icon:'fa-hubspot' },
    { type:'chart', chartType:'gauge', label:'仪表盘', icon:'fa-gauge-high' },
  ]},
  { name: '数据', items: [
    { type:'metric', label:'指标卡', icon:'fa-hashtag' },
    { type:'table', label:'表格', icon:'fa-table' },
  ]},
  { name: '文本', items: [
    { type:'title', label:'标题', icon:'fa-heading' },
    { type:'text', label:'文本', icon:'fa-font' },
    { type:'time', label:'时间', icon:'fa-clock' },
  ]},
  { name: '装饰', items: [
    { type:'border', variant:'a', label:'边框A', icon:'fa-border-all' },
    { type:'border', variant:'b', label:'边框B', icon:'fa-vector-square' },
    { type:'border', variant:'line', label:'分割线', icon:'fa-grip-lines' },
  ]},
]

const palettes = [
  ['#5b8ff9','#5ad8a6','#5d7092','#f6bd16','#e86452','#6dc8ec'],
  ['#00d4ff','#0090ff','#7b61ff','#00ffa3','#ffcf3f','#ff6b6b'],
  ['#36cfc9','#40a9ff','#9254de','#f759ab','#ffa940','#73d13d'],
  ['#ff9a3c','#ff6b6b','#c644fc','#5f6dff','#00c2ff','#00e0c7'],
]
const canvasThemes = [
  { name:'深蓝', bg:'#0a1834' }, { name:'午夜', bg:'#070b16' },
  { name:'墨绿', bg:'#0a1f1c' }, { name:'紫黑', bg:'#140a24' }, { name:'纯黑', bg:'#000000' },
]

const activeWidget = computed(() => widgets.value.find(w => w.id === activeId.value) || null)

const canvasStyle = computed(() => ({
  width: report.width + 'px', height: report.height + 'px',
  background: report.backgroundColor,
  backgroundImage: report.backgroundImage ? `url(${report.backgroundImage})` : undefined,
  backgroundSize: 'cover',
  transform: `scale(${scale.value})`, transformOrigin: 'top left'
}))
const widgetStyle = (w) => ({
  left: w.position.left + 'px', top: w.position.top + 'px',
  width: w.position.width + 'px', height: w.position.height + 'px',
  zIndex: w.position.zIndex || 1
})
const hexToRgba = (hex, a=1) => {
  if (!hex) return `rgba(0,0,0,${a})`
  const h = hex.replace('#',''); const n = parseInt(h.length===3 ? h.split('').map(x=>x+x).join('') : h, 16)
  return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`
}
const innerStyle = (w) => ({
  background: hexToRgba(w.style.background, w.style.bgOpacity ?? 1),
  border: (w.style.borderWidth||0) > 0 ? `${w.style.borderWidth}px solid ${w.style.borderColor}` : 'none',
  borderRadius: (w.style.radius||0) + 'px',
  color: w.style.color,
  fontSize: (w.style.fontSize||14) + 'px'
})

const widgetIcon = (w) => {
  if (w.type==='chart') {
    const m = { bar:'fa-chart-column', line:'fa-chart-line', pie:'fa-chart-pie', scatter:'fa-braille', radar:'fa-hubspot', gauge:'fa-gauge-high' }
    return m[w.chartType] || 'fa-chart-simple'
  }
  const m = { metric:'fa-hashtag', table:'fa-table', title:'fa-heading', text:'fa-font', time:'fa-clock', border:'fa-border-all' }
  return m[w.type] || 'fa-square'
}
// PLACEHOLDER_SCRIPT_2

// ===== 默认样式 =====
const defaultStyle = () => ({
  background: '#0e2247', bgOpacity: 0.6, borderColor: '#2b6cff', borderWidth: 1,
  radius: 8, color: '#e6ecf5', fontSize: 16, valueColor: '#00d4ff'
})

let seq = 0
const genId = () => 'w_' + Date.now() + '_' + (seq++)

// ===== 添加组件 =====
const addWidget = (c, pos) => {
  const idx = widgets.value.length
  const base = {
    id: genId(), type: c.type, chartType: c.chartType, variant: c.variant,
    title: '', content: '',
    position: {
      left: pos ? pos.x : 60 + (idx % 6) * 40,
      top: pos ? pos.y : 60 + (idx % 6) * 40,
      width: 480, height: 300, zIndex: idx + 1
    },
    style: defaultStyle()
  }
  // 各类型默认值
  if (c.type === 'chart') {
    base.title = c.label
    base.position.width = 500; base.position.height = 320
    base.echartsOption = demoChartOption(c.chartType)
    base.data = []
    base.dataStatus = 'unbound'
  } else if (c.type === 'metric') {
    base.title = '待绑定指标'; base.value = '—'; base.unit = ''
    base.dataStatus = 'unbound'
    base.position.width = 260; base.position.height = 130
  } else if (c.type === 'title') {
    base.content = '数据可视化大屏'; base.style.fontSize = 40; base.style.color = '#fff'
    base.style.background = '#000000'; base.style.bgOpacity = 0; base.style.borderWidth = 0
    base.position.width = 700; base.position.height = 70
  } else if (c.type === 'text') {
    base.content = '文本内容'; base.style.fontSize = 18
    base.style.background = '#000000'; base.style.bgOpacity = 0; base.style.borderWidth = 0
    base.position.width = 240; base.position.height = 50
  } else if (c.type === 'time') {
    base.style.fontSize = 22; base.style.background='#000000'; base.style.bgOpacity=0; base.style.borderWidth=0
    base.position.width = 320; base.position.height = 50
  } else if (c.type === 'table') {
    base.title = '数据表格'
    base.echartsOption = null; base.data = []; base.dataStatus = 'unbound'
    base.position.width = 560; base.position.height = 300
  } else if (c.type === 'border') {
    base.style.background = '#0e2247'; base.style.bgOpacity = 0.25
    base.position.width = 520; base.position.height = 340; base.position.zIndex = 0
  }
  widgets.value.push(base)
  activeId.value = base.id
  if (c.type === 'chart') { nextTick(() => renderChart(base)) }
  return base
}

// 拖拽添加
let dragComp = null
const onCompDragStart = (c) => { dragComp = c }
const onCanvasDrop = (e) => {
  if (!dragComp) return
  const canvas = e.currentTarget.querySelector('.designer-canvas')
  const rect = canvas.getBoundingClientRect()
  const x = Math.round((e.clientX - rect.left) / scale.value)
  const y = Math.round((e.clientY - rect.top) / scale.value)
  addWidget(dragComp, { x, y })
  dragComp = null
}

// ===== 未绑定图表结构（不填充任何业务示例数值） =====
const demoChartOption = (type) => {
  const color = palettes[1]
  const base = { color, textStyle:{color:'#cfe0ff'},
    tooltip:{trigger: type==='pie'?'item':'axis'},
    legend:{textStyle:{color:'#cfe0ff'}, top:'bottom'} }
  if (type === 'pie') {
    return { ...base, series:[{ type:'pie', radius:['40%','70%'], data:[] }] }
  }
  if (type === 'gauge') {
    return { ...base, series:[{ type:'gauge', progress:{show:true}, detail:{valueAnimation:true,color:'#00d4ff'}, data:[] }] }
  }
  if (type === 'radar') {
    return { ...base, radar:{ indicator:[], axisName:{color:'#cfe0ff'} }, series:[{ type:'radar', data:[] }] }
  }
  if (type === 'scatter') {
    return { ...base, xAxis:{axisLabel:{color:'#cfe0ff'}}, yAxis:{axisLabel:{color:'#cfe0ff'}}, series:[{ type:'scatter', data:[] }] }
  }
  return { ...base, grid:{left:'8%',right:'5%',bottom:'12%',top:'12%'},
    xAxis:{type:'category',data:[],axisLabel:{color:'#cfe0ff'}},
    yAxis:{type:'value',axisLabel:{color:'#cfe0ff'}},
    series:[{ type: type==='line'?'line':'bar', data:[], smooth:true, areaStyle: type==='line'?{}:undefined }] }
}

const tableHeaders = (w) => (w.data && w.data.length) ? Object.keys(w.data[0]) : []
const tableRows = (w) => w.data || []
// 只有带有查询绑定且返回过数据的组件才可标记为已核验，其余旧快照统一显示待绑定。
const widgetDataStatus = (w = {}) => {
  const hasBinding = Boolean(w.query?.sql || w.generatedSql || w.datasourceId || w.config?.datasourceId || w.config?.sql)
  const hasRows = Array.isArray(w.data) && w.data.length > 0
  // 只接受带真实查询绑定且有返回行的 verified；历史 demo/ready 标记一律降为待绑定。
  return (w.dataStatus === 'verified' || !w.dataStatus) && hasBinding && hasRows ? 'verified' : 'unbound'
}

// PLACEHOLDER_SCRIPT_3

// ===== AI 生成图表 =====
const generateWidget = async () => {
  if (!aiDatasourceId.value) { ElMessage.warning('请选择数据源'); return }
  if (!aiRequirement.value?.trim()) { ElMessage.warning('请输入需求描述'); return }
  aiLoading.value = true
  try {
    const res = await reportApi.aiGenerate({ datasourceId: aiDatasourceId.value, requirement: aiRequirement.value.trim() })
    const r = res.data
    if (!r || !r.success) { ElMessage.error(r?.errorMessage || 'AI生成失败'); return }
    const w = addWidget({ type:'chart', chartType: r.chartType || 'bar', label: r.title || '图表' })
    w.title = r.title || 'AI图表'
    w.query = { datasourceId: aiDatasourceId.value, requirement: aiRequirement.value.trim(), sql: r.generatedSql }
    w.echartsOption = r.echartsOption || w.echartsOption
    w.data = r.data || []
    w.dataStatus = Array.isArray(w.data) && w.data.length ? 'verified' : 'unbound'
    aiRequirement.value = ''
    await nextTick(); renderChart(w)
    ElMessage.success('已生成并添加到大屏')
  } catch (e) {
    ElMessage.error('AI生成失败：' + readableError(e, '请检查模型、数据源和权限后重试。'))
  } finally { aiLoading.value = false }
}

// ===== AI 整屏生成 =====
/** AI 整屏生成：按主题与数据源生成完整画布（超时 300s）；生成前会替换当前画布，需用户确认。 */
const generateScreen = async () => {
  if (!aiDatasourceId.value) { ElMessage.warning('请选择数据源'); return }
  if (!aiTheme.value?.trim()) { ElMessage.warning('请输入大屏主题'); return }
  if (widgets.value.length > 0) {
    try { await ElMessageBox.confirm('生成整屏将替换当前画布上的组件，是否继续？', '提示', { type:'warning' }) }
    catch (e) { return }
  }
  aiLoading.value = true
  try {
    const res = await reportApi.aiGenerateScreen({
      datasourceId: aiDatasourceId.value, theme: aiTheme.value.trim(),
      width: report.width, height: report.height
    })
    const r = res.data
    if (!r || !r.success) { ElMessage.error(r?.errorMessage || 'AI生成大屏失败'); return }

    // 清空现有组件
    Object.values(chartInstances).forEach(i => i.dispose())
    for (const k in chartInstances) delete chartInstances[k]
    for (const k in chartEls) delete chartEls[k]
    widgets.value = []
    activeId.value = null

    // 应用画布配置
    if (r.canvas) {
      if (r.canvas.width) report.width = r.canvas.width
      if (r.canvas.height) report.height = r.canvas.height
      if (r.canvas.backgroundColor) report.backgroundColor = r.canvas.backgroundColor
    }
    if (r.title) report.reportName = r.title
    report.datasourceId = aiDatasourceId.value

    // 装载组件(补齐默认样式)
    widgets.value = (r.widgets || []).map((w, i) => ({
      ...w,
      id: w.id || genId(),
      style: { ...defaultStyle(), ...(w.style || {}) },
      dataStatus: widgetDataStatus(w)
    }))

    await nextTick()
    fitScreen()
    renderAll()
    ElMessage.success(`已生成大屏：${widgets.value.length} 个组件`)
  } catch (e) {
    ElMessage.error('AI生成大屏失败：' + readableError(e, '请检查模型、数据源和权限后重试。'))
  } finally { aiLoading.value = false }
}

// ===== echarts 渲染 =====
const registerChartEl = (id, el) => { if (el) chartEls[id] = el }
const renderChart = (w) => {
  if (!w || w.type !== 'chart') return
  const el = chartEls[w.id]; if (!el) return
  let inst = chartInstances[w.id]
  if (!inst) { inst = echarts.init(el); chartInstances[w.id] = inst }
  try {
    const option = JSON.parse(JSON.stringify(w.echartsOption || {}))
    if (w.data && w.data.length) {
      option.dataset = option.dataset || {}
      if (!Array.isArray(option.dataset)) option.dataset.source = w.data
    }
    inst.setOption(option, true); inst.resize()
  } catch (e) { console.error('渲染图表失败', e) }
}
const renderAll = () => widgets.value.forEach(renderChart)

const applyPalette = (w, p) => {
  if (!w.echartsOption) w.echartsOption = {}
  w.echartsOption.color = [...p]
  renderChart(w)
}

// ===== 选择/删除 =====
const selectWidget = (id) => { activeId.value = id }
const removeWidget = (id) => {
  const i = widgets.value.findIndex(w => w.id === id)
  if (i >= 0) widgets.value.splice(i, 1)
  if (chartInstances[id]) { chartInstances[id].dispose(); delete chartInstances[id] }
  delete chartEls[id]
  if (activeId.value === id) activeId.value = null
}

// ===== 拖拽移动 =====
let dragState = null
const startDrag = (e, w) => {
  activeId.value = w.id
  dragState = { w, startX:e.clientX, startY:e.clientY, ol:w.position.left, ot:w.position.top }
  window.addEventListener('mousemove', onDrag); window.addEventListener('mouseup', stopDrag)
}
const onDrag = (e) => {
  if (!dragState) return
  dragState.w.position.left = Math.round(dragState.ol + (e.clientX - dragState.startX)/scale.value)
  dragState.w.position.top = Math.round(dragState.ot + (e.clientY - dragState.startY)/scale.value)
}
const stopDrag = () => { dragState=null; window.removeEventListener('mousemove', onDrag); window.removeEventListener('mouseup', stopDrag) }

// ===== 缩放组件 =====
let resizeState = null
const startResize = (e, w) => {
  activeId.value = w.id
  resizeState = { w, startX:e.clientX, startY:e.clientY, ow:w.position.width, oh:w.position.height }
  window.addEventListener('mousemove', onResize); window.addEventListener('mouseup', stopResize)
}
const onResize = (e) => {
  if (!resizeState) return
  resizeState.w.position.width = Math.max(80, Math.round(resizeState.ow + (e.clientX - resizeState.startX)/scale.value))
  resizeState.w.position.height = Math.max(50, Math.round(resizeState.oh + (e.clientY - resizeState.startY)/scale.value))
}
const stopResize = () => { if (resizeState) renderChart(resizeState.w); resizeState=null; window.removeEventListener('mousemove', onResize); window.removeEventListener('mouseup', stopResize) }

// ===== 视图缩放 =====
const zoomIn = () => { scale.value = Math.min(2, +(scale.value + 0.05).toFixed(2)); renderAll() }
const zoomOut = () => { scale.value = Math.max(0.1, +(scale.value - 0.05).toFixed(2)); renderAll() }
const fitScreen = () => {
  const wrap = canvasWrapRef.value; if (!wrap) return
  const pad = 60
  const s = Math.min((wrap.clientWidth-pad)/report.width, (wrap.clientHeight-pad)/report.height, 1)
  scale.value = +s.toFixed(2); nextTick(renderAll)
}

// PLACEHOLDER_SCRIPT_4

// ===== 加载/保存 =====
const loadDatasources = async () => {
  datasourceLoading.value = true
  datasourceError.value = ''
  try {
    const res = await get('/api/data-analysis/datasources')
    const rows = Array.isArray(res?.data) ? res.data : (Array.isArray(res) ? res : [])
    datasources.value = rows.filter(source => source?.enabled !== false)
    if (!datasources.value.length) {
      datasourceError.value = '当前账号没有可用数据源，请先检查数据源授权或配置。'
      return
    }
    if (!aiDatasourceId.value) aiDatasourceId.value = report.datasourceId || datasources.value[0].id
  } catch (e) {
    datasources.value = []
    datasourceError.value = readableError(e, '数据源列表加载失败，请检查后端服务连接。')
  } finally {
    datasourceLoading.value = false
  }
}
/** 加载报表（编辑态）：回填画布配置与组件（旧数据可能缺 style，用 defaultStyle 兜底补齐）。 */
const loadReport = async () => {
  const id = route.params.id; if (!id) return
  try {
    const res = await reportApi.getById(id); const data = res.data; if (!data) return
    Object.assign(report, {
      id:data.id, reportName:data.reportName, description:data.description, datasourceId:data.datasourceId,
      width:data.width||1920, height:data.height||1080, backgroundColor:data.backgroundColor||'#0a1834',
      backgroundImage:data.backgroundImage||'', refreshSeconds:data.refreshSeconds||0, enabled:data.enabled
    })
    aiDatasourceId.value = data.datasourceId
    if (data.content) {
      try {
        const parsed = JSON.parse(data.content)
        widgets.value = (parsed.widgets || []).map(w => ({ ...w, style: { ...defaultStyle(), ...(w.style||{}) }, dataStatus: widgetDataStatus(w) }))
      } catch (e) {
        // 内容损坏时保留当前画布，避免一次解析失败把用户已有布局清空。
        ElMessage.error('报表内容格式无效，已保留当前画布，请修复后再保存。')
      }
    }
    await nextTick(); fitScreen(); renderAll()
  } catch (e) { ElMessage.error(readableError(e, '加载报表失败，请检查报表权限和服务状态。')) }
}
const buildContent = () => JSON.stringify({
  canvas: { width:report.width, height:report.height, backgroundColor:report.backgroundColor, backgroundImage:report.backgroundImage, refreshSeconds:report.refreshSeconds },
  widgets: widgets.value
})
/**
 * 保存报表：无 id 时新建草稿，否则更新（内容为画布 + 组件快照）。
 * 约束：必须带上画布尺寸（宽高与背景一并保存），否则查看端会按默认 1920×1080 渲染导致错位。
 */
const save = async () => {
  saving.value = true
  try {
    const payload = {
      reportName:report.reportName, description:report.description,
      datasourceId:report.datasourceId || aiDatasourceId.value,
      width:report.width, height:report.height, backgroundColor:report.backgroundColor,
      backgroundImage:report.backgroundImage, refreshSeconds:report.refreshSeconds,
      enabled:report.enabled ?? 1, content: buildContent()
    }
    if (report.id) { await reportApi.update(report.id, payload) }
    else { const res = await reportApi.create(payload); report.id = res.data?.id; if (report.id) router.replace(`/report/designer/${report.id}`) }
    ElMessage.success('已保存')
  } catch (e) { ElMessage.error(readableError(e, '保存失败，请检查权限和数据源后重试。')) } finally { saving.value = false }
}
const preview = () => {
  if (!report.id) { ElMessage.warning('请先保存大屏'); return }
  window.open(router.resolve(`/report/view/${report.id}`).href, '_blank')
}
const goBack = () => router.push('/data-query')
const handleWinResize = () => renderAll()
const tickClock = () => {
  const d = new Date()
  nowTime.value = d.toLocaleString('zh-CN', { hour12:false })
}

onMounted(async () => {
  await loadDatasources()
  await loadReport()
  if (!route.params.id) fitScreen()
  tickClock(); clockTimer = setInterval(tickClock, 1000)
  window.addEventListener('resize', handleWinResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleWinResize)
  if (clockTimer) clearInterval(clockTimer)
  Object.values(chartInstances).forEach(i => i.dispose())
})
</script>
<!-- PLACEHOLDER_STYLE -->
<style scoped>
.report-designer { position: fixed; inset: 0; display: flex; flex-direction: column; background: #0a0f1c; color: #e6ecf5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; overflow: hidden; }

/* 顶栏 */
.designer-topbar { height: 54px; flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; padding: 0 16px; background: linear-gradient(90deg,#111a30,#16203a 50%,#111a30); border-bottom: 1px solid rgba(64,158,255,.25); box-shadow: 0 2px 20px rgba(0,0,0,.5); z-index: 10; }
.topbar-left,.topbar-center,.topbar-right { display: flex; align-items: center; gap: 12px; }
.report-title { display: flex; align-items: center; gap: 8px; color: #7fd4ff; }
.title-input { background: transparent; border: none; border-bottom: 1px solid transparent; color: #fff; font-size: 16px; font-weight: 600; padding: 4px 2px; outline: none; }
.title-input:focus { border-bottom-color: #409eff; }
.canvas-size { font-size: 13px; color: #6b7a99; }
.zoom-ctrl { display: flex; align-items: center; gap: 6px; padding: 4px 8px; background: rgba(255,255,255,.04); border-radius: 8px; }
.zoom-val { font-size: 13px; min-width: 42px; text-align: center; color: #b8c4dc; }
.tb-btn { display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 14px; border: 1px solid rgba(64,158,255,.3); border-radius: 8px; background: rgba(64,158,255,.08); color: #cdd8ee; font-size: 13px; cursor: pointer; transition: all .2s; }
.tb-btn:hover { background: rgba(64,158,255,.2); border-color: #409eff; color: #fff; }
.tb-btn.primary { background: linear-gradient(135deg,#409eff,#2b7fff); border-color: transparent; color: #fff; box-shadow: 0 4px 14px rgba(43,127,255,.4); }
.tb-btn.primary:disabled { opacity: .6; cursor: not-allowed; }
.tb-btn.ghost { border-color: transparent; background: rgba(255,255,255,.05); padding: 0 12px; }
.tb-btn.ghost.sm { height: 26px; width: 26px; padding: 0; justify-content: center; }

/* 三栏 */
.designer-body { flex: 1; display: flex; min-height: 0; }
.designer-left { width: 260px; flex-shrink: 0; background: #0c1426; border-right: 1px solid rgba(255,255,255,.06); display: flex; flex-direction: column; }
.designer-right { width: 300px; flex-shrink: 0; background: #0c1426; border-left: 1px solid rgba(255,255,255,.06); display: flex; flex-direction: column; padding: 14px; }

/* 左侧 tab */
.left-tabs { display: flex; border-bottom: 1px solid rgba(255,255,255,.08); }
.ltab { flex: 1; text-align: center; padding: 12px 0; font-size: 13px; color: #8494b4; cursor: pointer; border-bottom: 2px solid transparent; transition: all .2s; }
.ltab:hover { color: #cfe0ff; }
.ltab.active { color: #7fd4ff; border-bottom-color: #409eff; }

/* 组件库 */
.comp-lib { flex: 1; overflow-y: auto; padding: 12px; }
.comp-cat { margin-bottom: 16px; }
.comp-cat-title { font-size: 12px; color: #6b7a99; margin-bottom: 8px; padding-left: 4px; }
.comp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.comp-item { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px 6px; border-radius: 8px; background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.06); cursor: grab; transition: all .15s; font-size: 12px; color: #b8c4dc; }
.comp-item:hover { background: rgba(64,158,255,.15); border-color: #409eff; color: #fff; transform: translateY(-2px); }
.comp-item i { font-size: 20px; color: #7fd4ff; }

/* AI面板 */
.ai-panel { padding: 14px; }
.ds-select { width: 100%; margin-bottom: 10px; }
.datasource-error { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: -2px 0 10px; padding: 8px 10px; border: 1px solid rgba(255, 193, 92, .35); border-radius: 6px; background: rgba(94, 60, 16, .28); color: #ffd78b; font-size: 11px; line-height: 1.5; }
.datasource-error button { flex-shrink: 0; padding: 4px 8px; border: 1px solid rgba(255, 211, 121, .5); border-radius: 4px; background: transparent; color: #ffe4af; cursor: pointer; }
.datasource-error button:disabled { opacity: .55; cursor: not-allowed; }
.ai-input { width: 100%; min-height: 84px; resize: vertical; padding: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,.12); background: rgba(0,0,0,.25); color: #e6ecf5; font-size: 13px; outline: none; box-sizing: border-box; }
.ai-input:focus { border-color: #409eff; }
.ai-generate-btn { width: 100%; margin-top: 10px; height: 40px; border: none; border-radius: 8px; background: linear-gradient(135deg,#6a3cff,#409eff); color: #fff; font-size: 14px; font-weight: 600; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 8px; box-shadow: 0 4px 16px rgba(90,90,255,.4); }
.ai-generate-btn:disabled { opacity: .7; cursor: not-allowed; }
.ai-hint { margin: 8px 0 0; font-size: 11px; color: #6b7a99; line-height: 1.5; }
.ai-mode { display: flex; gap: 6px; margin-bottom: 12px; background: rgba(0,0,0,.25); padding: 4px; border-radius: 8px; }
.ai-mode-btn { flex: 1; text-align: center; padding: 7px 4px; font-size: 12px; color: #8494b4; border-radius: 6px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 5px; transition: all .2s; }
.ai-mode-btn:hover { color: #cfe0ff; }
.ai-mode-btn.active { background: linear-gradient(135deg,#6a3cff,#409eff); color: #fff; }
.theme-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.theme-chip { font-size: 12px; padding: 4px 10px; border-radius: 12px; background: rgba(64,158,255,.12); border: 1px solid rgba(64,158,255,.3); color: #9fd0ff; cursor: pointer; transition: all .15s; }
.theme-chip:hover { background: rgba(64,158,255,.25); color: #fff; }

/* 图层 */
.layer-list { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 6px; }
.layer-item { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 8px; background: rgba(255,255,255,.03); border: 1px solid transparent; cursor: pointer; font-size: 13px; }
.layer-item:hover { background: rgba(64,158,255,.1); }
.layer-item.active { background: rgba(64,158,255,.18); border-color: #409eff; }
.layer-item > i:first-child { color: #7fd4ff; }
.layer-name { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.layer-del { color: #6b7a99; opacity: 0; }
.layer-item:hover .layer-del { opacity: 1; }
.layer-del:hover { color: #ff6b6b; }
.layer-empty { text-align: center; color: #4a5875; font-size: 12px; padding: 16px 0; }
/* PLACEHOLDER_STYLE_2 */

/* 画布 */
.designer-canvas-wrap { flex: 1; min-width: 0; overflow: auto; padding: 30px; background: radial-gradient(circle at 20% 20%, rgba(64,120,255,.08), transparent 40%), radial-gradient(circle at 80% 80%, rgba(120,60,255,.08), transparent 40%), #070b16; background-image: linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px); background-size: 24px 24px; }
.designer-canvas { position: relative; box-shadow: 0 0 60px rgba(0,0,0,.6), 0 0 0 1px rgba(64,158,255,.15); overflow: hidden; }
.canvas-empty { position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); color: rgba(255,255,255,.35); font-size: 28px; white-space: nowrap; }

/* 组件盒子 */
.widget-box { position: absolute; cursor: move; transition: box-shadow .15s; }
.widget-box.active { box-shadow: 0 0 0 2px #409eff, 0 8px 30px rgba(43,127,255,.4); }
.widget-inner { width: 100%; height: 100%; overflow: hidden; box-sizing: border-box; position: relative; }
.w-chart { width: 100%; height: 100%; }
.w-unbound-state { position: absolute; inset: 0; display: grid; place-content: center; justify-items: center; gap: 5px; color: #b8c8df; background: rgba(5, 15, 32, .72); font-size: 15px; text-align: center; }
.w-unbound-state small { color: #7185a4; font-size: 11px; }

/* 指标卡 */
.w-metric { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; }
.w-metric-value { font-size: 2.4em; font-weight: 700; line-height: 1; text-shadow: 0 0 16px rgba(0,212,255,.5); font-variant-numeric: tabular-nums; }
.w-metric-unit { font-size: .5em; margin-left: 4px; opacity: .8; }
.w-metric-label { font-size: .9em; opacity: .8; }

/* 文本 */
.w-text { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; text-align: center; font-weight: 600; letter-spacing: 1px; }

/* 表格 */
.w-table { position: relative; width: 100%; height: 100%; overflow: auto; }
.w-table table { width: 100%; border-collapse: collapse; font-size: .8em; }
.w-table th { background: rgba(64,158,255,.2); color: #cfe0ff; padding: 8px; text-align: left; position: sticky; top: 0; }
.w-table td { padding: 7px 8px; border-bottom: 1px solid rgba(255,255,255,.06); }
.w-table tr:nth-child(even) td { background: rgba(255,255,255,.02); }

/* 边框装饰 */
.w-border { width: 100%; height: 100%; position: relative; }
.w-border.border-a { border: 1px solid rgba(64,158,255,.5); box-shadow: inset 0 0 20px rgba(64,158,255,.15); }
.w-border.border-a::before,.w-border.border-a::after { content: ''; position: absolute; width: 20px; height: 20px; border: 2px solid #00d4ff; }
.w-border.border-a::before { top: -1px; left: -1px; border-right: none; border-bottom: none; }
.w-border.border-a::after { bottom: -1px; right: -1px; border-left: none; border-top: none; }
.w-border.border-b { border: 2px solid rgba(0,212,255,.4); border-radius: 10px; box-shadow: 0 0 20px rgba(0,212,255,.2), inset 0 0 20px rgba(0,212,255,.1); }
.w-border.border-line { border: none; background: linear-gradient(90deg, transparent, #00d4ff, transparent); height: 2px !important; align-self: center; }

.resize-handle { position: absolute; right: -1px; bottom: -1px; width: 16px; height: 16px; cursor: nwse-resize; background: linear-gradient(135deg, transparent 50%, #409eff 50%); z-index: 5; }

/* 右侧属性 */
.panel-title { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; color: #9fd0ff; margin-bottom: 14px; }
.panel-title > i { color: #409eff; }
.prop-scroll { flex: 1; overflow-y: auto; }
.prop-sec { margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid rgba(255,255,255,.06); }
.prop-sec-title { font-size: 12px; color: #6b7a99; margin-bottom: 10px; }
.prop-group { margin-bottom: 12px; }
.prop-group > label { display: block; font-size: 12px; color: #8494b4; margin-bottom: 6px; }
.prop-row { display: flex; gap: 10px; }
.prop-group.half { flex: 1; }
.prop-input { width: 100%; height: 34px; padding: 0 10px; border-radius: 7px; border: 1px solid rgba(255,255,255,.12); background: rgba(0,0,0,.25); color: #e6ecf5; font-size: 13px; outline: none; box-sizing: border-box; }
.prop-input:focus { border-color: #409eff; }
.prop-input.sql { height: auto; padding: 8px 10px; font-family: monospace; font-size: 12px; line-height: 1.5; resize: vertical; }
.prop-color { width: 100%; height: 34px; border: none; background: none; cursor: pointer; padding: 0; }
.color-row { display: flex; align-items: center; gap: 8px; }
.color-row .prop-color { width: 44px; flex-shrink: 0; }
.prop-range { flex: 1; }
.range-val { font-size: 12px; color: #8494b4; width: 38px; text-align: right; }
.palette-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.palette { display: flex; cursor: pointer; border-radius: 4px; overflow: hidden; border: 1px solid rgba(255,255,255,.1); transition: transform .15s; }
.palette:hover { transform: scale(1.08); border-color: #409eff; }
.palette > span { width: 14px; height: 22px; }
.prop-quick { display: flex; gap: 8px; margin-top: 6px; }
.qtheme { width: 28px; height: 28px; border-radius: 6px; cursor: pointer; border: 1px solid rgba(255,255,255,.15); }
.qtheme:hover { border-color: #409eff; }
.prop-danger { width: 100%; height: 36px; border: 1px solid rgba(255,107,107,.4); border-radius: 7px; background: rgba(255,107,107,.1); color: #ff8787; font-size: 13px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px; }
.prop-danger:hover { background: rgba(255,107,107,.2); }
.prop-tip { font-size: 12px; color: #4a5875; line-height: 1.6; }

/* 滚动条 */
.comp-lib::-webkit-scrollbar,.layer-list::-webkit-scrollbar,.prop-scroll::-webkit-scrollbar,.designer-canvas-wrap::-webkit-scrollbar { width: 8px; height: 8px; }
.comp-lib::-webkit-scrollbar-thumb,.layer-list::-webkit-scrollbar-thumb,.prop-scroll::-webkit-scrollbar-thumb,.designer-canvas-wrap::-webkit-scrollbar-thumb { background: rgba(64,158,255,.25); border-radius: 4px; }
</style>
