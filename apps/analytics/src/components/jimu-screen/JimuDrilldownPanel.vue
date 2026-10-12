<script setup>
/**
 * JimuDrilldownPanel —— 图表下钻明细面板（点击数据点后展示对应维度下的原始行）。
 *
 * 使用场景：JimuBigScreenReplica 收到组件的 data-click/drilldown 事件后打开。
 * 依赖接口：`api/request.can`（导出等操作的权限判定）。
 * 关键约束：
 * - 面板只展示父级传入的 rows/headers/sql/statistics，不自行取数，避免下钻产生额外查询；
 * - loading / error / executed 三态必须分别呈现：未执行与"执行后无数据"是不同语义，不能共用空态；
 * - truncated 为真时必须提示结果被截断，不能让用户把部分数据当全量；
 * - 导出走 emit('export')，由父级决定导出范围与格式。
 */
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { can } from '@/api/request'

const props = defineProps({
  widget: { type: Object, required: true },
  title: { type: String, default: '' },
  point: { type: Object, default: () => ({}) },
  rows: { type: Array, default: () => [] },
  headers: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  sql: { type: String, default: '' },
  sourceSql: { type: String, default: '' },
  executionTime: { type: Number, default: 0 },
  executed: { type: Boolean, default: false },
  truncated: { type: Boolean, default: false },
  statistics: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['close', 'export'])
const dialog = ref(null)
const closeButton = ref(null)
let previousFocus = null
const statusLabel = computed(() => props.loading ? '正在执行查询' : props.error ? '查询失败' : props.executed ? '查询完成' : '组件当前数据（未重新查询）')
onMounted(() => { previousFocus = document.activeElement; closeButton.value?.focus() })
onBeforeUnmount(() => previousFocus?.focus?.())
function handleKey(event) {
  if (event.key === 'Escape') { event.stopPropagation(); emit('close') }
  if (event.key !== 'Tab') return
  const items = [...(dialog.value?.querySelectorAll('button:not(:disabled), summary, a[href], [tabindex="0"]') || [])]
    .filter(item => item.getClientRects().length)
  if (!items.length) return
  if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1).focus() }
  else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus() }
}

const numericFacts = computed(() => {
  return Object.entries(props.statistics?.columns || {})
    .filter(([, stats]) => stats?.sum != null)
    .slice(0, 3)
    .map(([key, stats]) => ({
      key,
      label: props.headers.find(item => item.key === key)?.label || key,
      value: stats.sum,
    }))
})

const pointLabel = computed(() => {
  const value = props.point?.name ?? props.point?.value
  if (Array.isArray(value)) return value.join(' / ')
  return value == null || value === '' ? '组件全部数据' : String(value)
})

function display(value) {
  if (value == null || value === '') return '-'
  if (typeof value === 'number') {
    return String(value)
  }
  return String(value)
}
</script>

<template>
  <Teleport to="body">
    <div class="drilldown-shell" @mousedown.self="$emit('close')">
      <div class="matrix-grid" aria-hidden="true"></div>
      <section ref="dialog" class="drilldown-cockpit" role="dialog" aria-modal="true" aria-labelledby="drilldown-title" @keydown="handleKey">
        <header class="cockpit-header">
          <div class="header-mark"><i></i><b>DATA</b><span>DRILLDOWN</span></div>
          <div class="header-title">
            <small>SECONDARY ANALYSIS COCKPIT</small>
            <h2 id="drilldown-title">{{ title || `${widget.componentName} · 数据明细` }}</h2>
            <p><i class="fa-solid fa-crosshairs"></i>{{ pointLabel }}</p>
          </div>
          <div class="header-actions">
            <span>{{ executed ? '已执行查询' : '组件数据' }}</span>
            <button v-if="can('dashboards.export')" :disabled="loading || Boolean(error)" type="button" @click="$emit('export')"><i class="fa-solid fa-download"></i>导出 CSV</button>
            <button ref="closeButton" class="close" type="button" aria-label="关闭数据明细" @click="$emit('close')"><i class="fa-solid fa-xmark"></i></button>
          </div>
        </header>

        <div class="signal-rail">
          <span>DATASOURCE / {{ widget.config?.datasourceId || '-' }}</span>
          <b role="status">{{ statusLabel }}</b>
          <span>{{ executed ? '查询耗时 ' + executionTime + ' ms' : '尚无查询耗时' }}</span>
        </div>

        <div class="fact-grid">
          <article class="fact-card primary">
            <small>DETAIL ROWS</small>
            <strong>{{ rows.length }}</strong>
            <span>当前明细记录</span>
          </article>
          <article v-for="fact in numericFacts" :key="fact.key" class="fact-card">
            <small>{{ fact.label }}</small>
            <strong>{{ display(fact.value) }}</strong>
            <span>本次返回记录合计{{ truncated ? '（截断样本）' : '' }}</span>
          </article>
          <article v-if="!numericFacts.length" class="fact-card">
            <small>DATA FIELDS</small>
            <strong>{{ headers.length }}</strong>
            <span>已识别数据字段</span>
          </article>
        </div>

        <main class="cockpit-main">
          <section class="table-zone">
            <div class="zone-heading">
              <div><small>LIVE QUERY RESULT</small><strong>业务数据明细</strong></div>
              <span v-if="loading"><i class="fa-solid fa-spinner fa-spin"></i>正在读取已授权数据源</span>
              <span v-else>{{ rows.length }} 条记录{{ truncated ? '（达到返回上限）' : '' }}</span>
            </div>

            <div v-if="error" class="query-warning" role="alert">
              <i class="fa-solid fa-triangle-exclamation"></i>
              <div><strong>明细查询未完成</strong><span>{{ error }}</span></div>
            </div>

            <div class="data-table">
              <div class="table-head" :style="{ '--columns': `repeat(${Math.max(1, headers.length)}, minmax(128px, 1fr))` }">
                <span v-for="header in headers" :key="header.key">{{ header.label }}</span>
              </div>
              <div v-if="loading && !rows.length" class="loading-field">
                <i class="fa-solid fa-database"></i>
                <strong>正在执行二级数据查询</strong>
                <span>参数绑定 · 只读校验 · 结果集装配</span>
              </div>
              <div v-else-if="!rows.length" class="loading-field empty">
                <i class="fa-solid fa-radar"></i>
                <strong>当前数据点暂无明细</strong>
                <span>查询配置已保留，可调整筛选范围后重试</span>
              </div>
              <div v-else class="table-body">
                <div
                  v-for="(row, index) in rows"
                  :key="index"
                  class="table-row"
                  :style="{ '--columns': `repeat(${Math.max(1, headers.length)}, minmax(128px, 1fr))` }"
                >
                  <span v-for="header in headers" :key="header.key" :title="display(row?.[header.key])">
                    {{ display(row?.[header.key]) }}
                  </span>
                </div>
              </div>
            </div>
          </section>

          <aside class="insight-zone">
            <div class="zone-heading">
              <div><small>QUERY CONTEXT</small><strong>钻取上下文</strong></div>
            </div>
            <dl>
              <div><dt>组件</dt><dd>{{ widget.componentName }}</dd></div>
              <div><dt>类型</dt><dd>{{ widget.component }}</dd></div>
              <div><dt>筛选</dt><dd>{{ pointLabel }}</dd></div>
              <div><dt>字段</dt><dd>{{ headers.length }}</dd></div>
            </dl>
            <details :open="Boolean(error)">
              <summary><i class="fa-solid fa-code"></i>查看执行 SQL</summary>
              <pre>{{ sql || '本次未执行钻取 SQL；此处不以旧查询或静态配置冒充新结果。' }}</pre>
            </details>
            <div class="analysis-pulse">
              <span></span>
              <div><small>数据来源与查询状态</small><strong>{{ statusLabel }}</strong><p>点击其他数据点可切换筛选上下文</p></div>
            </div>
          </aside>
        </main>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
button:focus-visible, summary:focus-visible { outline: 2px solid #80dfff; outline-offset: 3px; }
button:disabled { opacity: .5; cursor: not-allowed; }
.drilldown-shell {
  position: fixed;
  z-index: 30000;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 28px;
  overflow: hidden;
  background:
    radial-gradient(circle at 50% 0%, rgba(35, 220, 255, .16), transparent 35%),
    radial-gradient(circle at 100% 100%, rgba(86, 255, 158, .08), transparent 34%),
    rgba(0, 6, 14, .94);
  color: #dffaff;
  font-family: "Microsoft YaHei", sans-serif;
  backdrop-filter: blur(16px);
}
.matrix-grid { position: absolute; inset: 0; opacity: .28; background-image: linear-gradient(rgba(40, 215, 255, .055) 1px, transparent 1px), linear-gradient(90deg, rgba(40, 215, 255, .055) 1px, transparent 1px); background-size: 28px 28px; mask-image: linear-gradient(to bottom, #000, transparent 92%); }
.drilldown-cockpit { position: relative; display: grid; grid-template-rows: auto auto auto minmax(0, 1fr); width: min(1500px, calc(100vw - 56px)); height: min(900px, calc(100vh - 56px)); padding: 16px 18px 18px; border: 1px solid rgba(47, 222, 255, .46); background: linear-gradient(145deg, rgba(4, 17, 30, .98), rgba(2, 10, 20, .97)); box-shadow: 0 0 70px rgba(23, 196, 240, .14), inset 0 0 55px rgba(23, 196, 240, .035); clip-path: polygon(0 16px, 16px 0, calc(100% - 44px) 0, 100% 44px, 100% 100%, 0 100%); }
.drilldown-cockpit::before,.drilldown-cockpit::after { content: ""; position: absolute; height: 2px; background: linear-gradient(90deg, transparent, #2ce6ff, #57ff9c, transparent); box-shadow: 0 0 15px #2ce6ff; }.drilldown-cockpit::before { left: 30px; right: 35%; top: 0; }.drilldown-cockpit::after { left: 42%; right: 35px; bottom: 0; }
.cockpit-header { display: grid; grid-template-columns: 148px minmax(0, 1fr) auto; align-items: center; min-height: 76px; border-bottom: 1px solid rgba(67, 151, 180, .2); }
.header-mark { display: flex; align-items: baseline; gap: 7px; color: #2ce6ff; font: 10px Consolas, monospace; letter-spacing: .12em; }.header-mark i { width: 8px; height: 30px; border-left: 2px solid #57ff9c; border-top: 2px solid #57ff9c; }.header-mark b { font-size: 18px; }.header-mark span { color: #5c8293; font-size: 7px; }
.header-title { text-align: center; }.header-title small { color: #507d91; font: 8px Consolas, monospace; letter-spacing: .24em; }.header-title h2 { margin: 4px 0; color: #ecfdff; font: 600 25px Bahnschrift, "Microsoft YaHei", sans-serif; letter-spacing: .08em; text-shadow: 0 0 16px rgba(44, 230, 255, .42); }.header-title p { margin: 0; color: #70a6ba; font-size: 10px; }.header-title p i { margin-right: 7px; color: #57ff9c; }
.header-actions { display: flex; align-items: center; gap: 8px; }.header-actions > span { margin-right: 8px; color: #57ff9c; font: 8px Consolas, monospace; }.header-actions > span i { display: inline-block; width: 5px; height: 5px; margin-right: 6px; border-radius: 50%; background: #57ff9c; box-shadow: 0 0 9px #57ff9c; }.header-actions button { height: 31px; padding: 0 12px; border: 1px solid rgba(44, 230, 255, .35); background: rgba(13, 52, 70, .38); color: #bcecf5; cursor: pointer; }.header-actions button i { margin-right: 6px; }.header-actions .close { width: 31px; padding: 0; border-color: rgba(255, 101, 119, .35); color: #ff8492; }.header-actions .close i { margin: 0; }
.signal-rail { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; min-height: 34px; padding: 0 12px; border-left: 2px solid #2ce6ff; border-right: 2px solid #8b7cff; background: linear-gradient(90deg, rgba(44, 230, 255, .07), transparent 25% 75%, rgba(139, 124, 255, .07)); color: #4d7587; font: 8px Consolas, monospace; letter-spacing: .08em; }.signal-rail span:last-child { text-align: right; }.signal-rail b { color: #b9e8f0; font-weight: 400; }.signal-rail b i { display: inline-block; width: 5px; height: 5px; margin-right: 7px; border-radius: 50%; background: #57ff9c; box-shadow: 0 0 8px #57ff9c; }
.fact-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; padding: 12px 0; }.fact-card { position: relative; display: grid; min-width: 0; min-height: 78px; padding: 11px 14px; overflow: hidden; border: 1px solid rgba(53, 132, 162, .2); background: linear-gradient(135deg, rgba(14, 46, 65, .52), rgba(4, 15, 27, .76)); }.fact-card::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 2px; background: #2ce6ff; box-shadow: 0 0 10px #2ce6ff; }.fact-card.primary::before { background: #57ff9c; box-shadow: 0 0 10px #57ff9c; }.fact-card small { color: #4f7b8e; font: 8px Consolas, monospace; letter-spacing: .13em; }.fact-card strong { overflow: hidden; color: #dffaff; font: 600 25px Bahnschrift, sans-serif; text-overflow: ellipsis; white-space: nowrap; }.fact-card span { color: #668e9e; font-size: 9px; }
.cockpit-main { display: grid; grid-template-columns: minmax(0, 1fr) 310px; gap: 12px; min-height: 0; }.table-zone,.insight-zone { min-width: 0; min-height: 0; border: 1px solid rgba(57, 140, 173, .2); background: rgba(2, 11, 21, .72); }.table-zone { display: grid; grid-template-rows: auto auto minmax(0, 1fr); padding: 12px; }.insight-zone { padding: 12px; }
.zone-heading { display: flex; align-items: center; justify-content: space-between; min-height: 42px; border-bottom: 1px solid rgba(57, 140, 173, .18); }.zone-heading div { display: grid; gap: 2px; }.zone-heading small { color: #477589; font: 7px Consolas, monospace; letter-spacing: .15em; }.zone-heading strong { color: #cceef5; font-size: 12px; }.zone-heading > span { color: #57ff9c; font-size: 9px; }.zone-heading > span i { margin-right: 6px; }
.query-warning { display: flex; gap: 10px; align-items: center; margin-top: 8px; padding: 8px 10px; border: 1px solid rgba(255, 190, 75, .35); background: rgba(70, 42, 7, .38); color: #ffc857; }.query-warning div { display: grid; }.query-warning strong { font-size: 10px; }.query-warning span { color: #9f8556; font-size: 8px; }
.data-table { min-height: 0; margin-top: 9px; overflow: auto; border: 1px solid rgba(58, 136, 166, .15); scrollbar-color: #245468 #071522; }.table-head,.table-row { display: grid; grid-template-columns: var(--columns); min-width: max-content; }.table-head { position: sticky; z-index: 2; top: 0; min-height: 38px; background: linear-gradient(90deg, rgba(20, 87, 112, .68), rgba(11, 47, 68, .76)); color: #2ce6ff; font-size: 10px; }.table-head span,.table-row span { display: flex; align-items: center; min-width: 128px; padding: 0 11px; border-right: 1px solid rgba(57, 140, 173, .12); }.table-body { min-width: max-content; }.table-row { min-height: 38px; border-bottom: 1px solid rgba(57, 140, 173, .11); color: #acd0d9; font-size: 9px; }.table-row:nth-child(even) { background: rgba(39, 152, 188, .045); }.table-row:hover { background: rgba(44, 230, 255, .08); color: #e7fbff; }
.loading-field { display: grid; place-items: center; align-content: center; min-height: 240px; color: #6aa3b8; }.loading-field i { margin-bottom: 12px; color: #2ce6ff; font-size: 28px; filter: drop-shadow(0 0 11px #2ce6ff); }.loading-field strong { color: #c8edf4; font-size: 13px; }.loading-field span { margin-top: 5px; font-size: 9px; }.loading-field.empty i { color: #70879a; filter: none; }
.insight-zone dl { display: grid; gap: 5px; margin: 10px 0; }.insight-zone dl div { display: grid; grid-template-columns: 58px minmax(0, 1fr); min-height: 31px; align-items: center; padding: 0 8px; border: 1px solid rgba(57, 140, 173, .1); background: rgba(7, 23, 36, .55); }.insight-zone dt { color: #4d7485; font-size: 8px; }.insight-zone dd { overflow: hidden; margin: 0; color: #b8dbe4; font-size: 9px; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.insight-zone details { border: 1px solid rgba(139, 124, 255, .2); background: rgba(21, 17, 49, .38); }.insight-zone summary { padding: 10px; color: #bcb4ff; font-size: 9px; cursor: pointer; }.insight-zone summary i { margin-right: 7px; }.insight-zone pre { max-height: 180px; margin: 0; padding: 10px; overflow: auto; border-top: 1px solid rgba(139, 124, 255, .15); color: #6ecfa4; font: 8px/1.6 Consolas, monospace; white-space: pre-wrap; word-break: break-all; }
.analysis-pulse { display: flex; align-items: center; gap: 12px; margin-top: 10px; padding: 12px; border: 1px solid rgba(87, 255, 156, .17); background: linear-gradient(90deg, rgba(87, 255, 156, .06), transparent); }.analysis-pulse > span { width: 36px; height: 36px; border: 1px solid #57ff9c; border-radius: 50%; box-shadow: 0 0 16px rgba(87, 255, 156, .24), inset 0 0 12px rgba(87, 255, 156, .12); animation: pulse 1.8s ease-in-out infinite; }.analysis-pulse div { display: grid; gap: 2px; }.analysis-pulse small { color: #4c8c68; font: 7px Consolas, monospace; }.analysis-pulse strong { color: #bfffd6; font-size: 10px; }.analysis-pulse p { margin: 0; color: #547663; font-size: 8px; }
@keyframes pulse { 50% { transform: scale(.84); opacity: .55; } }
@media (max-width: 900px) { .drilldown-shell { padding: 10px; }.drilldown-cockpit { width: calc(100vw - 20px); height: calc(100vh - 20px); }.cockpit-header { grid-template-columns: 1fr auto; }.header-mark { display: none; }.header-title { text-align: left; }.header-actions > span { display: none; }.fact-grid { grid-template-columns: repeat(2, 1fr); }.cockpit-main { grid-template-columns: 1fr; }.insight-zone { display: none; } }
@media (prefers-reduced-motion: reduce) { .analysis-pulse > span { animation: none; } }
</style>
