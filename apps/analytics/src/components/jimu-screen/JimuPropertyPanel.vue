<script setup>
/**
 * JimuPropertyPanel —— 大屏编辑器的右侧属性面板（页面样式 / 选中组件配置 / 数据绑定 / 交互设置）。
 *
 * 位置：JimuBigScreenReplica（路由 `/report/designer/:id?`）右侧栏。
 * 依赖接口：`api/request.can`（数据绑定等敏感操作权限）、`utils/safeContent`（背景图等资源地址安全校验）、
 * `utils/widgetText`（文本组件的配置归一化）、WidgetDatabaseBinding / WidgetHttpBinding（数据源绑定）。
 * 关键约束：
 * - 视觉锁定（visualLocked）或无编辑权限时，属性修改与数据绑定必须整体禁用，只能查看；
 * - 修改以 patch-page / patch-widget 上抛，并且用 interaction-start/interaction-end 包裹连续编辑
 *   （如拖拽滑块），使父级的撤销栈只记录一次操作；
 * - 配置写入前需经 safeAssetUrl 校验，禁止把任意 URL 直接作为背景图/资源使用。
 */
import { computed, ref, watch } from 'vue'
import { can } from '@/api/request'
import { safeAssetUrl, scopedAssetUrl } from '@/utils/safeContent'
import WidgetDatabaseBinding from '../business/WidgetDatabaseBinding.vue'
import WidgetHttpBinding from '../business/WidgetHttpBinding.vue'
import { isTextWidget, normalizeTextConfig } from '@/utils/widgetText'

const props = defineProps({
  page: { type: Object, required: true },
  selected: { type: Object, default: null },
  visualLocked: { type: Boolean, default: false }
})

const emit = defineEmits(['patch-page', 'patch-widget', 'chart-data', 'collapse', 'open-ai', 'interaction-start', 'interaction-end'])
const activeTab = ref('config')
const dataText = ref('')
const inputError = ref('')
const backgroundInput = ref(null)
const backgroundPreview = `${import.meta.env.BASE_URL}jimu-screen/bg4.png`
const backgroundAsset = computed(() => scopedAssetUrl(props.page.backgroundImage, import.meta.env.BASE_URL))

const componentOption = computed(() => props.selected?.config?.option || {})
const isText = computed(() => isTextWidget(props.selected))
const textBody = computed(() => isText.value ? normalizeTextConfig(props.selected).option.body : {})
const directTextFields = new Set(['body.text', 'body.fontSize', 'body.color', 'body.textAlign'])
const advancedEntries = computed(() => flattenOptions(componentOption.value)
  .filter(entry => !isText.value || !directTextFields.has(entry.path)))
const dataType = computed(() => Number(props.selected?.config?.dataType || 1))
const isChart = computed(() => /Bar|Line|Area|Ring|Pie|Map|Radar|Gauge|Funnel|Scatter|Chart/i.test(props.selected?.component || ''))

const fieldLabels = {
  rowNum: '可见行数', waitTime: '轮播间隔', carousel: '轮播方式', hoverPause: '悬停暂停',
  headShow: '显示表头', headerHeight: '表头高度', headerBGC: '表头背景', fontColor: '字体颜色',
  oddRowBGC: '奇数行背景', evenRowBGC: '偶数行背景', mainColor: '主颜色', subColor: '辅助颜色',
  dur: '动画时长', show: '显示', text: '文本', fontSize: '字号', color: '颜色', smooth: '平滑曲线',
  fontWeight: '字重', textAlign: '对齐方式', letterSpacing: '字间距', lineHeight: '行高',
  type: '类型', position: '位置', left: '左侧间距', right: '右侧间距', top: '顶部间距', bottom: '底部间距',
  width: '宽度', height: '高度', radius: '圆角', opacity: '透明度', rotate: '旋转角度',
  name: '名称', value: '数值', label: '标签', formatter: '格式化', trigger: '触发方式',
  axisLabel: '坐标轴标签', axisLine: '坐标轴线', splitLine: '分隔线', itemStyle: '图形样式',
  textStyle: '文字样式', grid: '绘图区', legend: '图例', tooltip: '提示框', series: '系列数据',
  padding: '内边距', gap: '间距', borderRadius: '圆角', borderColor: '边框颜色',
  shadow: '阴影', fill: '填充颜色', image: '图片', fit: '适应方式'
}

function flattenOptions(value, prefix = '') {
  if (!value || typeof value !== 'object') return []
  return Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key
    if (Array.isArray(child)) return [{ path, key, value: child, kind: 'json' }]
    if (child && typeof child === 'object') return flattenOptions(child, path)
    return [{ path, key, value: child, kind: typeof child }]
  })
}

function fieldLabel(entry) {
  return fieldLabels[entry.key] || '自定义配置'
}

function patchAdvanced(entry, value) {
  let next = value
  if (entry.kind === 'number') next = Number(value)
  if (entry.kind === 'json') {
    try { next = JSON.parse(value) } catch { return }
  }
  patchWidget(`config.option.${entry.path}`, next)
}

watch([() => props.selected?.id, () => props.visualLocked], () => {
  activeTab.value = props.visualLocked && props.selected ? 'data' : 'config'
  const value = props.selected?.config?.chartDataParsed ?? props.selected?.config?.chartData ?? []
  dataText.value = JSON.stringify(value, null, 2)
}, { immediate: true })

function patchWidget(path, value) {
  if (props.visualLocked) {
    const allowed = [
      'config.dataType', 'config.datasourceId', 'config.tableName', 'config.sql',
      'config.parameters', 'config.chartData', 'config.chartDataParsed',
      'config.resultColumns', 'config.queryId', 'config.verifiedAt',
      'config.dataStatus', 'config.dataMapping'
    ]
    if (!allowed.some(prefix => path === prefix || path.startsWith(`${prefix}.`))) return
  }
  emit('patch-widget', path, value)
}

function patchText(key, value) {
  patchWidget('config', normalizeTextConfig(props.selected, { [key]: value }))
}

function applyData() {
  inputError.value = ''
  try {
    const value = JSON.parse(dataText.value)
    if (!Array.isArray(value)) throw new Error('数据内容必须为 JSON 数组')
    emit('chart-data', value)
  } catch (error) { inputError.value = error.message || '数据 JSON 无效，请检查后重试' }
}

function setDataType(value) {
  patchWidget('config.dataType', value)
}

function colorPickerValue(value, fallback = '#ffffff') {
  const match = String(value || '').match(/^#[0-9a-fA-F]{6}/)
  return match ? match[0] : fallback
}

async function uploadBackground(event) {
  const file = event.target.files?.[0]
  if (!file) return
  inputError.value = ''
  if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type) || file.size > 500 * 1024) {
    inputError.value = '请选择不超过 500 KB 的 PNG/JPEG/WebP/GIF 图片；大图请使用已上传的资源地址'
    return
  }
  try {
    const url = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result)
      reader.onerror = () => reject(new Error('读取背景图片失败'))
      reader.readAsDataURL(file)
    })
    emit('patch-page', 'backgroundImage', url)
    emit('patch-page', 'visualMode', '')
  } catch (error) { inputError.value = error.message }
}
function setBackground(value) {
  if (value && !safeAssetUrl(value)) { inputError.value = '背景地址不安全'; return }
  inputError.value = ''
  emit('patch-page', 'backgroundImage', value)
  emit('patch-page', 'visualMode', '')
}
</script>

<template>
  <aside class="property-panel">
    <div class="property-scroll"
      @focusin="event => { if (event.target.matches('input, textarea, select')) emit('interaction-start') }"
      @focusout="event => { if (event.target.matches('input, textarea, select')) emit('interaction-end') }">
    <p v-if="inputError" role="alert" class="input-error">{{ inputError }}</p>
    <button class="collapse-handle" title="收起配置" @click="emit('collapse')"><i class="fa-solid fa-angle-right"></i></button>
    <div v-if="visualLocked" class="source-template-lock"><i class="fa-solid fa-lock"></i><span><strong>源模板视觉锁定</strong><small>可修改静态数据或数据绑定</small></span></div>

    <template v-if="!selected">
      <header class="panel-heading">大屏配置</header>
      <div class="page-settings" :class="{ 'visual-readonly': visualLocked }">
        <label class="form-row"><span>名称</span><input :value="page.name" @input="emit('patch-page', 'name', $event.target.value)"></label>
        <div class="size-row">
          <label><span>W</span><input type="number" :value="page.design.width" @input="emit('patch-page', 'design.width', Number($event.target.value))"></label>
          <label><span>H</span><input type="number" :value="page.design.height" @input="emit('patch-page', 'design.height', Number($event.target.value))"></label>
        </div>
        <label class="form-row color-row">
          <span>背景色</span>
          <span class="color-input"><input type="color" :value="page.backgroundColor" @input="emit('patch-page', 'backgroundColor', $event.target.value)"><input :value="page.backgroundColor" @change="emit('patch-page', 'backgroundColor', $event.target.value)"></span>
        </label>
        <div class="background-setting">
          <span>背景图</span>
          <div>
            <div class="segmented"><button @click="backgroundInput.click()">上传图片</button><button @click="setBackground(backgroundPreview)">使用星空背景</button></div>
            <input ref="backgroundInput" type="file" hidden accept="image/png,image/jpeg,image/webp,image/gif" @change="uploadBackground">
            <img v-if="backgroundAsset" :src="backgroundAsset" alt="当前大屏背景">
            <label>背景资源地址<input :value="page.backgroundImage || ''" @change="setBackground($event.target.value)"></label>
            <button @click="setBackground('')">清除背景图片</button>
          </div>
        </div>
        <div class="palette-row">
          <span>配色</span>
          <div class="palette"><i v-for="color in page.design.sysDefColor || []" :key="color.color" :style="{ background: color.color }"></i><em>默认</em></div>
        </div>
        <section class="setting-section">
          <h3>水印配置</h3>
          <label class="check-row"><input type="checkbox" :checked="page.design.waterMark?.show" @change="emit('patch-page', 'design.waterMark.show', $event.target.checked)"><span>预览显示</span></label>
          <label v-if="page.design.waterMark?.show" class="form-row"><span>水印文字</span><input :value="page.design.waterMark?.text || ''" placeholder="例如：内部资料" @input="emit('patch-page', 'design.waterMark.text', $event.target.value)"></label>
        </section>
        <section class="setting-section">
          <label class="check-row"><input type="checkbox" :checked="Number(page.refreshSeconds) > 0" @change="emit('patch-page', 'refreshSeconds', $event.target.checked ? 60 : 0)"><span>启用预览数据定时刷新</span></label>
          <label v-if="Number(page.refreshSeconds) > 0" class="form-row"><span>间隔秒数</span><input type="number" min="5" max="86400" :value="page.refreshSeconds" @change="emit('patch-page', 'refreshSeconds', Math.max(5, Math.min(86400, Number($event.target.value) || 60)))"></label>
        </section>
      </div>
    </template>

    <template v-else>
      <header class="component-heading">
        <strong>组件配置</strong><span class="data-state">{{ dataType === 3 ? '数据库' : dataType === 2 ? 'API' : '静态数据' }}</span><em>（{{ selected.config?.option?.title?.text || selected.componentName }}）</em>
      </header>
      <nav class="config-tabs">
        <button :class="{ active: activeTab === 'config' }" title="配置" :disabled="visualLocked" @click="activeTab = 'config'"><i class="fa-solid fa-gear"></i></button>
        <button :class="{ active: activeTab === 'data' }" title="数据" @click="activeTab = 'data'"><i class="fa-solid fa-database"></i></button>
        <button :class="{ active: activeTab === 'link' }" title="联动" :disabled="visualLocked" @click="activeTab = 'link'"><i class="fa-solid fa-link"></i></button>
      </nav>

      <div v-if="activeTab === 'config'" class="component-settings">
        <div class="settings-card">
          <header><i class="fa-solid fa-caret-down"></i><span>基础设置</span><em>{{ selected.metricName || selected.componentName }}</em></header>
          <div class="card-body">
            <a>操作说明 <i class="fa-regular fa-circle-question"></i></a>
            <section>
              <h3>图层配置</h3>
              <label class="form-row"><span>名称</span><input :value="selected.metricName || selected.componentName" @input="patchWidget('componentName', $event.target.value)"></label>
              <label class="check-row"><input type="checkbox" :checked="selected.visible !== false" @change="patchWidget('visible', $event.target.checked)"><span>显示</span></label>
              <label class="form-row color-row"><span>背景</span><span class="color-input"><input type="color" :value="colorPickerValue(selected.config?.background)" @input="patchWidget('config.background', $event.target.value)"><input :value="selected.config?.background || '#FFFFFF00'" @change="patchWidget('config.background', $event.target.value)"></span></label>
              <label class="form-row color-row"><span>边框</span><span class="color-input"><input type="color" :value="colorPickerValue(selected.config?.borderColor)" @input="patchWidget('config.borderColor', $event.target.value)"><input :value="selected.config?.borderColor || '#FFFFFF00'" @change="patchWidget('config.borderColor', $event.target.value)"></span></label>
            </section>
            <section v-if="isText" class="text-settings">
              <h3>文字内容</h3>
              <label><span>{{ selected.component === 'JDragEditor' ? '富文本内容（HTML）' : '正文' }}</span><textarea :value="textBody.text" rows="4" @input="patchText('text', $event.target.value)"></textarea></label>
              <div class="position-grid compact">
                <label><span>字号</span><input aria-label="文字字号" type="number" min="1" max="300" :value="textBody.fontSize ?? 28" @change="patchText('fontSize', Math.max(1, Math.min(300, Number($event.target.value) || 28)))"></label>
                <label><span>颜色</span><input aria-label="文字颜色" :value="textBody.color ?? '#d0e0f0'" @change="patchText('color', $event.target.value)"></label>
              </div>
              <label class="form-row"><span>对齐</span><select aria-label="文字对齐" :value="textBody.textAlign ?? 'center'" @change="patchText('textAlign', $event.target.value)"><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select></label>
            </section>
            <section>
              <h3>位置配置</h3>
              <div class="position-grid">
                <label><span>X</span><input type="number" :value="selected.x" @input="patchWidget('x', Number($event.target.value))"></label>
                <label><span>Y</span><input type="number" :value="selected.y" @input="patchWidget('y', Number($event.target.value))"></label>
                <label><span>W</span><input type="number" :value="selected.w" @input="patchWidget('w', Number($event.target.value))"></label>
                <label><span>H</span><input type="number" :value="selected.h" @input="patchWidget('h', Number($event.target.value))"></label>
              </div>
              <label class="slider-row"><span>旋转</span><input aria-label="旋转角度滑块" type="range" min="-180" max="180" :value="selected.rotation || 0" @input="patchWidget('rotation', Number($event.target.value))"><input aria-label="旋转角度" type="number" :value="selected.rotation || 0" @input="patchWidget('rotation', Number($event.target.value))"></label>
            </section>
            <section v-if="isChart">
              <h3>提示窗配置</h3>
              <label class="check-row"><input type="checkbox" :checked="componentOption.tooltip?.show !== false" @change="patchWidget('config.option.tooltip.show', $event.target.checked)"><span>显示</span></label>
              <div class="position-grid compact">
                <label><span>字号</span><input type="number" :value="componentOption.tooltip?.textStyle?.fontSize || 14" @change="patchWidget('config.option.tooltip.textStyle.fontSize', Number($event.target.value))"></label>
                <label><span>颜色</span><input :value="componentOption.tooltip?.textStyle?.color || '#EEF1FA'" @change="patchWidget('config.option.tooltip.textStyle.color', $event.target.value)"></label>
              </div>
            </section>
            <details v-if="componentOption.title" open>
              <summary>标题设置 <input type="checkbox" :checked="componentOption.title.show !== false" @click.stop @change="patchWidget('config.option.title.show', $event.target.checked)"></summary>
              <label class="form-row"><span>文本</span><input :value="componentOption.title.text" @input="patchWidget('config.option.title.text', $event.target.value)"></label>
            </details>
            <details v-if="componentOption.xAxis">
              <summary>X轴设置 <input type="checkbox" :checked="componentOption.xAxis.show !== false" @click.stop @change="patchWidget('config.option.xAxis.show', $event.target.checked)"></summary>
            </details>
            <details v-if="componentOption.yAxis">
              <summary>Y轴设置 <input type="checkbox" :checked="componentOption.yAxis.show !== false" @click.stop @change="patchWidget('config.option.yAxis.show', $event.target.checked)"></summary>
            </details>
            <details>
              <summary>高级设置</summary>
              <p class="config-summary">{{ selected.component }} · {{ selected.id }}</p>
              <div class="advanced-form actual-options">
                <label v-for="entry in advancedEntries" :key="entry.path" :class="{ 'check-row': entry.kind === 'boolean' }">
                  <template v-if="entry.kind === 'boolean'"><input type="checkbox" :checked="entry.value" @change="patchAdvanced(entry, $event.target.checked)"><span>{{ fieldLabel(entry) }}</span></template>
                  <template v-else-if="entry.kind === 'number'"><span>{{ fieldLabel(entry) }}</span><input type="number" :value="entry.value" @input="patchAdvanced(entry, $event.target.value)"></template>
                  <template v-else-if="entry.kind === 'string' && /color/i.test(entry.key)"><span>{{ fieldLabel(entry) }}</span><input type="color" :value="entry.value || '#000000'" @input="patchAdvanced(entry, $event.target.value)"></template>
                  <template v-else-if="entry.kind === 'json'"><span>{{ fieldLabel(entry) }}</span><textarea :value="JSON.stringify(entry.value)" @change="patchAdvanced(entry, $event.target.value)"></textarea></template>
                  <template v-else><span>{{ fieldLabel(entry) }}</span><input :value="entry.value ?? ''" @input="patchAdvanced(entry, $event.target.value)"></template>
                </label>
              </div>
            </details>
          </div>
        </div>
      </div>

      <div v-else-if="activeTab === 'data'" class="data-settings">
        <p v-if="selected.referenceSkin" class="reference-data-note">{{ selected.config?.referenceSample && selected.config?.dataStatus === 'sample' ? '当前显示参考图示例。原图的完整序列无法从 JPG 恢复，下方示例用于说明填写格式。应用数据后，此区域按你的数据重绘。' : '数据区域按当前输入重绘，保留模板位置与外框。清空数组会显示暂无数据。' }}</p>
        <div class="data-mode">
          <button :class="{ active: dataType === 1 }" @click="setDataType(1)">静态数据</button>
          <button :class="{ active: dataType === 2 }" @click="setDataType(2)">API</button>
          <button :class="{ active: dataType === 3 }" @click="setDataType(3)">数据库</button>
        </div>
        <template v-if="dataType === 1">
          <label>数据内容<textarea v-model="dataText" spellcheck="false" @blur="applyData"></textarea></label>
          <button class="primary" @click="applyData">应用数据</button>
        </template>
        <template v-else-if="dataType === 2">
          <WidgetHttpBinding :widget="selected" @patch="patchWidget" @data="emit('chart-data', $event)" />
        </template>
        <template v-else>
          <WidgetDatabaseBinding :widget="selected" @patch="patchWidget" @data="emit('chart-data', $event)" />
        </template>
      </div>

      <div v-else class="link-settings">
        <label>跳转地址<input :value="selected.config?.turnConfig?.url || ''" @change="patchWidget('config.turnConfig.url', $event.target.value)"></label>
        <label>打开方式<select :value="selected.config?.turnConfig?.type || '_blank'" @change="patchWidget('config.turnConfig.type', $event.target.value)"><option value="_blank">新窗口</option><option value="_self">本窗口</option></select></label>
        <label class="check-row"><input type="checkbox" :checked="selected.config?.linkageEnabled === true" @change="patchWidget('config.linkageEnabled', $event.target.checked)"><span>启用组件联动</span></label>
        <section class="setting-section">
          <h3>点击钻取</h3><p>保存后由服务端读取此配置。使用 :point 等命名参数，点击数据点传入筛选值。</p>
          <label>钻取标题<input :value="selected.config?.drilldown?.title || ''" @change="patchWidget('config.drilldown.title', $event.target.value)"></label>
          <label>参数名称<input :value="selected.config?.drilldown?.parameter || 'point'" @change="patchWidget('config.drilldown.parameter', $event.target.value)"></label>
          <label>取值字段<input :value="selected.config?.drilldown?.triggerField || ''" placeholder="例如：department；留空使用点击项名称" @change="patchWidget('config.drilldown.triggerField', $event.target.value)"></label>
          <label>只读明细 SQL<textarea :value="selected.config?.drilldown?.sql || ''" rows="6" placeholder="SELECT ... WHERE department = :point" @change="patchWidget('config.drilldown.sql', $event.target.value)"></textarea></label>
        </section>
      </div>
    </template>

    </div>
    <footer v-if="can('dashboards.execute')" class="property-actions">
      <button class="ai-config" @click="emit('open-ai')"><i></i><span>AI 修改配置</span></button>
    </footer>
  </aside>
</template>

<style scoped>
.text-settings textarea { display:block;width:100%;resize:vertical;min-height:80px;white-space:pre-wrap; }
.input-error { padding:10px;color:#ffd2c9;background:#512b2b;font-size:12px;line-height:1.7; }
.background-setting button { border:1px solid #7d93ae;background:#22364e;color:#e8f2ff;padding:5px;cursor:pointer; }
.background-setting input:not([type=file]) { width:100%;padding:6px; }.background-setting label { display:grid;gap:6px;margin-top:8px;font-size:12px; }
.setting-section p { color:#bccbdf;font-size:12px;line-height:1.7; }
.property-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 280px;
  height: 100%;
  min-height: 0;
  border-left: 1px solid #14171c;
  background: #1d2026;
  color: #d5d9de;
  scrollbar-color: #393f47 #1d2026;
  scrollbar-width: thin;
}

.property-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-color: #393f47 #1d2026;
  scrollbar-width: thin;
}

.property-actions {
  flex-shrink: 0;
  display: flex;
  justify-content: center;
  padding: 12px;
  border-top: 1px solid #393f47;
  background: #1d2026;
}

.collapse-handle {
  position: fixed;
  right: 271px;
  top: 545px;
  z-index: 100;
  display: grid;
  place-items: center;
  width: 21px;
  height: 26px;
  border: 1px solid #555b63;
  border-radius: 13px 0 0 13px;
  background: #272b31;
  color: #a8afb7;
  cursor: pointer;
}

.panel-heading,
.component-heading {
  display: flex;
  align-items: center;
  min-height: 48px;
  padding: 0 14px;
  color: #f2f4f6;
  font-size: 15px;
  font-weight: 700;
}

.component-heading { gap: 6px; }
.component-heading em {
  overflow: hidden;
  max-width: 118px;
  color: #aab1b8;
  font-size: 12px;
  font-style: normal;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.data-state {
  padding: 3px 7px;
  border: 1px solid #31610f;
  border-radius: 2px;
  background: #122a08;
  color: #73f522;
  font-size: 11px;
  font-weight: 400;
}

.page-settings,
.component-settings,
.data-settings,
.link-settings { padding: 14px 12px 20px; }

.form-row {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  align-items: center;
  gap: 9px;
  margin-bottom: 12px;
  font-size: 13px;
}

input,
select,
textarea {
  min-width: 0;
  border: 1px solid #3f444b;
  border-radius: 2px;
  background: #161616;
  color: #e7e9ec;
  font: inherit;
  outline: none;
}

input:focus,
select:focus,
textarea:focus { border-color: #1688df; }
.form-row > input { width: 100%; height: 29px; padding: 0 9px; }

.size-row,
.position-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 13px;
}

.size-row label,
.position-grid label {
  display: grid;
  grid-template-columns: 18px 1fr;
  align-items: center;
  gap: 5px;
  font-size: 12px;
}

.size-row input,
.position-grid input { width: 100%; height: 28px; padding: 0 7px; }

.color-input { display: grid; grid-template-columns: 26px 1fr; }
.color-input input[type='color'] { width: 26px; height: 29px; padding: 3px; border-right: 0; }
.color-input input:not([type='color']) { width: 100%; height: 29px; padding: 0 7px; }

.background-setting,
.palette-row {
  display: grid;
  grid-template-columns: 48px 1fr;
  gap: 9px;
  margin: 18px 0;
  font-size: 13px;
}

.segmented { display: grid; grid-template-columns: 1fr 1fr; margin-bottom: 10px; }
.segmented button,
.data-mode button {
  height: 29px;
  border: 1px solid #42474e;
  background: #17191d;
  color: #c7cbd0;
  font-size: 12px;
}

.segmented button.active,
.data-mode button.active { border-color: #1688df; background: #1688df; color: #fff; }
.background-setting img { width: 120px; height: 80px; object-fit: cover; }

.palette {
  display: flex;
  align-items: center;
  height: 29px;
  padding: 0 7px;
  border: 1px solid #3f444b;
  background: #181b1f;
}

.palette i { width: 14px; height: 17px; }
.palette em { margin-left: auto; color: #ddd; font-size: 11px; font-style: normal; }

.setting-section {
  padding: 18px 0;
  border-top: 1px solid #3b3f45;
}

.setting-section h3 { margin: 0 0 14px; color: #d9dde2; font-size: 13px; }
.check-row { display: flex; align-items: center; gap: 7px; color: #d5d9de; font-size: 13px; }
.check-row input { width: 16px; height: 16px; accent-color: #1688df; }

.config-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  height: 74px;
  padding: 0 22px;
  border-bottom: 1px solid #373c43;
}

.config-tabs button {
  border: 0;
  background: transparent;
  color: #929aa3;
  font-size: 17px;
  cursor: pointer;
}
.config-tabs button.active { color: #138dea; }

.settings-card { border: 1px solid #3a3f46; background: #151515; }
.settings-card > header {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 10px;
  background: #2b2f35;
  font-size: 13px;
}

.settings-card > header em {
  overflow: hidden;
  max-width: 125px;
  padding: 2px 7px;
  border: 1px solid #44698f;
  color: #bdd6f2;
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-body { padding: 13px 11px; }
.source-template-lock { display: flex; align-items: center; gap: 8px; margin: 9px 9px 0; padding: 8px 9px; color: #91add0; background: #182b40; border: 1px solid #2d4a68; border-radius: 4px; }
.source-template-lock > i { color: #69a9e8; font-size: 10px; }
.source-template-lock span { display: grid; gap: 1px; }
.source-template-lock strong { color: #d8e8f8; font-size: 10px; }
.source-template-lock small { color: #8098b2; font-size: 8px; }
.visual-readonly { opacity: .55; pointer-events: none; }
.config-tabs button:disabled { color: #525b66; cursor: not-allowed; }
.card-body > a { display: block; margin-bottom: 8px; color: #168dea; font-size: 11px; text-align: right; }
.card-body section { padding: 10px 0 14px; border-bottom: 1px solid #393d43; }
.card-body section h3 { margin: 0 0 12px; font-size: 13px; font-weight: 500; }
.position-grid.compact label { grid-template-columns: 34px 1fr; }

.slider-row {
  display: grid;
  grid-template-columns: 38px 1fr 46px;
  align-items: center;
  gap: 7px;
  font-size: 12px;
}

.slider-row input[type='range'] { accent-color: #1688df; }
.slider-row input[type='number'] { width: 46px; height: 26px; }

details { border-bottom: 1px solid #383c42; }
summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 40px;
  color: #d4d8dd;
  font-size: 12px;
  cursor: pointer;
}
summary input { accent-color: #1688df; }
details .form-row { padding: 0 0 8px; }
.config-summary { color: #7f8993; font-size: 11px; word-break: break-all; }
.advanced-form { display: grid; gap: 9px; padding: 0 0 12px; }
.advanced-form > label:not(.check-row) { display: grid; grid-template-columns: 1fr 104px; align-items: center; gap: 8px; color: #b8c0c9; font-size: 12px; }
.advanced-form input:not([type='checkbox']), .advanced-form select { width: 104px; height: 28px; padding: 0 6px; }

.data-mode { display: grid; grid-template-columns: repeat(3, 1fr); margin-bottom: 16px; }
.data-settings label,
.link-settings label:not(.check-row) { display: grid; gap: 7px; margin-bottom: 14px; font-size: 12px; }
.data-settings textarea { height: 310px; padding: 9px; resize: vertical; font-family: Consolas, monospace; font-size: 11px; }
.data-settings input,
.link-settings input,
.link-settings select { height: 30px; padding: 0 8px; }

.primary {
  width: 100%;
  height: 32px;
  border: 0;
  background: #1688df;
  color: #fff;
  cursor: pointer;
}

.ai-config {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  width: 178px;
  height: 40px;
  border: 0;
  border-radius: 20px;
  background: linear-gradient(90deg, #713df3, #3977ff);
  color: #fff;
  font-weight: 700;
  box-shadow: 0 0 18px rgba(101, 81, 255, .62);
  cursor: pointer;
}

.ai-config i {
  width: 10px;
  height: 10px;
  background: #fff;
  clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%);
}
</style>
