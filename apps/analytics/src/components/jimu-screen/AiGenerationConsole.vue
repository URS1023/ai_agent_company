<script setup>
/**
 * AiGenerationConsole —— AI 生成大屏/组件的全过程控制台（阶段进度、矢量动效、错误与重试）。
 *
 * 使用场景：JimuBigScreenReplica 的 AI 生成弹窗内（生成进行中与失败时）。
 * 依赖接口：`utils/aiGenerationProgress.AI_GENERATION_STAGES`（阶段定义与配色）、
 * AiCodeRainCanvas / AiVectorCore（视觉动效）。
 * 关键约束：
 * - 阶段文案与配色只能来自 AI_GENERATION_STAGES，进度含义由父级传入的 progress 决定，组件不自行计时；
 * - error 非空时呈现失败态与"重试"入口，重试只派发事件，由父级决定是否重新发起（不自动重试）；
 * - 纯展示组件：不发起请求、不写状态。
 */
import { computed } from 'vue'
import { AI_GENERATION_STAGES } from '@/utils/aiGenerationProgress'
import AiCodeRainCanvas from './AiCodeRainCanvas.vue'
import AiVectorCore from './AiVectorCore.vue'

const props = defineProps({
  progress: { type: Object, required: true },
  scenarioTitle: { type: String, default: '自定义分析任务' },
  datasourceName: { type: String, default: '' },
  mode: { type: String, default: 'screen' },
  error: { type: String, default: '' }
})

defineEmits(['retry', 'back'])

const activeStage = computed(() => AI_GENERATION_STAGES[props.progress.activeIndex] || AI_GENERATION_STAGES[0])
// 未拿到数据源名称时明确显示待选择，避免把历史环境名称当成当前数据源。
const displayDatasourceName = computed(() => props.datasourceName?.trim() || '未选择数据源')
/** 阶段文案优先级：成功/错误/暂停态覆盖当前阶段名，避免"生成失败但显示正在渲染"的误导。 */
const stateLabel = computed(() => {
  if (props.progress.state === 'success') return '视觉图层汇聚完成'
  if (props.progress.state === 'error') return '计算链路中断'
  if (props.progress.isPaused) return 'AI 正在校准推理路径'
  return activeStage.value.label
})
const activeColor = computed(() => activeStage.value.color || '#27d7ff')
const percentLabel = computed(() => Number(props.progress.percent || 0).toFixed(props.progress.percent % 1 ? 1 : 0))
const computeNodes = computed(() => [
  { icon: 'fa-cubes-stacked', label: '向量检索引擎', value: 'BAAI/bge-m3', threshold: 1, accent: '#27d7ff' },
  { icon: 'fa-book-atlas', label: '知识库关系层', value: `${props.progress.telemetry.candidateTables || 0} 张候选表`, threshold: 2, accent: '#55f991' },
  { icon: 'fa-diagram-project', label: 'Schema Graph', value: `${props.progress.telemetry.relationEdges || 0} 条关系边`, threshold: 2, accent: '#8b7cff' },
  { icon: 'fa-code', label: 'SQL 多智能体', value: `${props.progress.telemetry.sqlDrafts || 0} 份草案`, threshold: 3, accent: '#ffc857' },
  { icon: 'fa-chart-simple', label: '可视化渲染器', value: `${props.progress.telemetry.renderNodes || 0} 个节点`, threshold: 4, accent: '#ff6577' }
])
const consoleLogs = computed(() => [
  `vector.recall --model BAAI/bge-m3 --source ${displayDatasourceName.value}`,
  `schema.graph --semantic true --edges ${props.progress.telemetry.relationEdges || 0}`,
  `agent.parallel --sql ${props.progress.telemetry.sqlDrafts || 0} --render ${props.progress.telemetry.renderNodes || 0}`,
  props.progress.event || activeStage.value.detail
])

function stageState(index) {
  if (props.progress.state === 'success') return 'complete'
  if (props.progress.state === 'error' && index === props.progress.activeIndex) return 'error'
  if (index < props.progress.activeIndex) return 'complete'
  if (index === props.progress.activeIndex) return props.progress.isPaused ? 'active paused' : 'active'
  return 'pending'
}

function nodeState(node) {
  if (props.progress.state === 'error' && node.threshold === props.progress.activeIndex) return 'error'
  if (props.progress.activeIndex > node.threshold || props.progress.state === 'success') return 'online complete'
  if (props.progress.activeIndex === node.threshold) return props.progress.isPaused ? 'online paused' : 'online'
  return 'standby'
}
</script>

<template>
  <section class="generation-console" :class="[`is-${progress.state}`, { 'is-paused': progress.isPaused }]" :style="{ '--active-color': activeColor }">
    <AiCodeRainCanvas :complete="progress.state === 'success'" :intensity="1.18" />
    <div class="star-noise" aria-hidden="true"></div>
    <div class="console-grid" aria-hidden="true"></div>
    <div class="scan-beam" aria-hidden="true"></div>

    <div class="console-content">
      <header class="console-header">
        <div class="brand-lockup">
          <div class="brand-mark"><i class="fa-solid fa-brain"></i><span></span></div>
          <div>
            <small>LYNX / NEURAL DATA ORCHESTRATOR</small>
            <strong>{{ mode === 'screen' ? 'GLOBAL DASHBOARD SYNTHESIS' : 'WIDGET INTELLIGENCE LAB' }}</strong>
          </div>
        </div>
        <div class="simulation-badge"><i></i>AI 编排模拟进度</div>
        <div class="header-signal"><span>LIVE</span><b>{{ percentLabel }}<small>%</small></b></div>
      </header>

      <div class="task-ribbon">
        <span><i class="fa-solid fa-database"></i>{{ displayDatasourceName }}</span>
        <strong>{{ scenarioTitle }}</strong>
        <span><i class="fa-solid fa-shield-halved"></i>只读 SQL 审查</span>
      </div>

      <div class="orchestration-grid">
        <aside class="stage-rail">
          <div class="section-title"><small>01 / PIPELINE</small><strong>推理任务链</strong></div>
          <article
            v-for="(stage, index) in AI_GENERATION_STAGES"
            :key="stage.id"
            :class="stageState(index)"
            :style="{ '--stage-color': stage.color }"
          >
            <div class="stage-index"><span>0{{ index + 1 }}</span><i :class="['fa-solid', stage.icon]"></i></div>
            <div class="stage-copy"><strong>{{ stage.label }}</strong><p>{{ stage.detail }}</p></div>
            <div class="stage-state">
              <i v-if="index < progress.activeIndex || progress.state === 'success'" class="fa-solid fa-check"></i>
              <i v-else-if="index === progress.activeIndex && progress.isPaused" class="fa-solid fa-pause"></i>
              <span v-else></span>
            </div>
          </article>
        </aside>

        <main class="core-zone">
          <div class="core-heading">
            <small>02 / VECTOR CORE</small>
            <strong>{{ stateLabel }}</strong>
            <p>{{ progress.event || activeStage.detail }}</p>
          </div>
          <AiVectorCore
            :percent="progress.percent"
            :active-color="activeColor"
            :is-paused="progress.isPaused"
            :state="progress.state"
            :event="progress.event"
            :telemetry="progress.telemetry"
          />
          <div class="stage-energy">
            <span v-for="(stage, index) in AI_GENERATION_STAGES" :key="stage.id" :class="{ active: index <= progress.activeIndex }">
              <i :style="{ width: index < progress.activeIndex ? '100%' : (index === progress.activeIndex ? `${progress.stageProgress || 0}%` : '0%') }"></i>
            </span>
          </div>
        </main>

        <aside class="compute-rack">
          <div class="section-title"><small>03 / COMPUTE FABRIC</small><strong>智能计算矩阵</strong></div>
          <div class="radar-panel">
            <div class="vector-radar"><span></span><i v-for="index in 7" :key="index" :style="{ '--point': index }"></i></div>
            <div><small>VECTOR HITS</small><strong>{{ progress.telemetry.vectorHits }}</strong><p>语义召回持续扩展</p></div>
          </div>
          <div class="node-list">
            <article v-for="node in computeNodes" :key="node.label" :class="nodeState(node)" :style="{ '--node-accent': node.accent }">
              <i :class="['fa-solid', node.icon]"></i>
              <span><small>{{ node.label }}</small><strong>{{ node.value }}</strong></span>
              <b></b>
            </article>
          </div>
          <div class="quality-panel">
            <div><small>布局质量评分</small><strong>{{ progress.qualityScore || 62 }}<sup>/100</sup></strong></div>
            <span><i :style="{ width: `${progress.qualityScore || 62}%` }"></i></span>
            <p>碰撞检测 · 字段可读性 · 视觉层级</p>
          </div>
        </aside>
      </div>

      <div class="console-terminal">
        <div class="terminal-heading"><span><i></i><i></i><i></i></span><strong>ORCHESTRATION STREAM</strong><small>{{ progress.isPaused ? 'PATH HOLD' : 'PACKET FLOW' }}</small></div>
        <div class="terminal-lines">
          <code v-for="(log, index) in consoleLogs" :key="`${index}-${log}`" :class="{ pulse: index === consoleLogs.length - 1 }"><span>0{{ index + 1 }}</span>&gt; {{ log }}<b v-if="index === consoleLogs.length - 1">_</b></code>
        </div>
        <div class="telemetry">
          <span><small>候选表</small><b>{{ progress.telemetry.candidateTables || 0 }}</b></span>
          <span><small>关系边</small><b>{{ progress.telemetry.relationEdges }}</b></span>
          <span><small>SQL 草案</small><b>{{ progress.telemetry.sqlDrafts }}</b></span>
          <span><small>渲染节点</small><b>{{ progress.telemetry.renderNodes }}</b></span>
          <span><small>数据吞吐</small><b>{{ progress.telemetry.throughput || 0 }}<em>M</em></b></span>
        </div>
      </div>

      <div class="master-progress">
        <div class="progress-meta"><span>{{ progress.isPaused ? '随机驻留：计算仍在后台流动' : '多智能体任务正在并行推进' }}</span><b>{{ percentLabel }}%</b></div>
        <div class="progress-track"><span :style="{ width: `${progress.percent}%` }"></span><i :style="{ left: `${progress.percent}%` }"></i></div>
      </div>

      <div v-if="progress.state === 'error'" class="error-panel">
        <span><i class="fa-solid fa-triangle-exclamation"></i>{{ error || 'AI 生成失败，请检查模型或数据源配置' }}</span>
        <div><button @click="$emit('back')">返回修改</button><button class="retry" @click="$emit('retry')"><i class="fa-solid fa-rotate-right"></i>重新执行</button></div>
      </div>
      <div v-else-if="progress.state === 'success'" class="success-signal">
        <i class="fa-solid fa-circle-check"></i><span>质量审查通过，正在将数据组件写入画布</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.generation-console {
  --active-color: #27d7ff;
  position: relative;
  min-height: 640px;
  overflow: hidden;
  border: 1px solid rgba(71, 190, 231, .42);
  background: radial-gradient(circle at 50% 42%, rgba(10, 41, 68, .74), transparent 42%), linear-gradient(145deg, #020711, #061321 48%, #030812);
  color: #dff8ff;
  font-family: 'Microsoft YaHei', sans-serif;
  isolation: isolate;
}
.generation-console::before,.generation-console::after { content: ''; position: absolute; z-index: 1; pointer-events: none; }
.generation-console::before { inset: 0; border: 1px solid rgba(129, 225, 255, .08); box-shadow: inset 0 0 80px rgba(4, 176, 255, .07), inset 0 -60px 100px rgba(0, 0, 0, .38); }
.generation-console::after { inset: 10px; border: 1px solid transparent; border-image: linear-gradient(135deg, var(--active-color), transparent 18%, transparent 82%, #8b7cff) 1; opacity: .34; }
.star-noise { position: absolute; inset: 0; z-index: 0; opacity: .28; background-image: radial-gradient(circle at 12% 24%, #fff 0 1px, transparent 1.5px), radial-gradient(circle at 78% 17%, #27d7ff 0 1px, transparent 1.6px), radial-gradient(circle at 64% 71%, #8b7cff 0 1px, transparent 1.6px), radial-gradient(circle at 28% 82%, #55f991 0 1px, transparent 1.6px); background-size: 137px 131px, 173px 149px, 211px 181px, 251px 219px; }
.console-grid { position: absolute; inset: 0; z-index: 0; opacity: .12; background-image: linear-gradient(rgba(59, 159, 201, .22) 1px, transparent 1px), linear-gradient(90deg, rgba(59, 159, 201, .2) 1px, transparent 1px); background-size: 32px 32px; perspective: 400px; }
.scan-beam { position: absolute; z-index: 1; left: 0; right: 0; height: 1px; background: linear-gradient(90deg, transparent, var(--active-color), transparent); box-shadow: 0 0 18px var(--active-color); opacity: .48; animation: console-scan 4.8s linear infinite; }
.console-content { position: relative; z-index: 2; padding: 17px 22px 16px; text-shadow: 0 0 10px rgba(39, 215, 255, .1); }

.console-header { display: grid; grid-template-columns: 1fr auto 168px; align-items: center; gap: 18px; min-height: 54px; padding: 0 2px 12px; border-bottom: 1px solid rgba(74, 173, 212, .23); }
.brand-lockup { display: flex; align-items: center; gap: 12px; min-width: 0; }.brand-lockup > div:last-child { display: grid; gap: 2px; min-width: 0; }
.brand-lockup small { color: #527e94; font: 8px Consolas, monospace; letter-spacing: .22em; }.brand-lockup strong { color: #d9f6ff; font: 600 15px Bahnschrift, 'Microsoft YaHei', sans-serif; letter-spacing: .1em; }
.brand-mark { position: relative; display: grid; place-items: center; width: 38px; height: 38px; border: 1px solid var(--active-color); color: var(--active-color); box-shadow: inset 0 0 17px color-mix(in srgb, var(--active-color) 11%, transparent), 0 0 16px color-mix(in srgb, var(--active-color) 18%, transparent); clip-path: polygon(0 0, calc(100% - 9px) 0, 100% 9px, 100% 100%, 9px 100%, 0 calc(100% - 9px)); }
.brand-mark span { position: absolute; right: 3px; top: 3px; width: 4px; height: 4px; background: #ffc857; box-shadow: 0 0 8px #ffc857; animation: signal-blink .9s step-end infinite; }
.simulation-badge { padding: 6px 11px; border: 1px solid rgba(255, 200, 87, .32); background: rgba(44, 32, 10, .46); color: #d7b663; font-size: 9px; letter-spacing: .08em; }.simulation-badge i { display: inline-block; width: 5px; height: 5px; margin-right: 7px; border-radius: 50%; background: #ffc857; box-shadow: 0 0 8px #ffc857; }
.header-signal { display: flex; align-items: baseline; justify-content: flex-end; gap: 12px; }.header-signal > span { color: #55f991; font: 9px Consolas, monospace; letter-spacing: .16em; }.header-signal b { min-width: 94px; color: #eaffff; font: 600 35px/1 Bahnschrift, sans-serif; text-align: right; text-shadow: 0 0 18px color-mix(in srgb, var(--active-color) 65%, transparent); }.header-signal small { margin-left: 2px; color: var(--active-color); font-size: 11px; }

.task-ribbon { display: grid; grid-template-columns: 175px minmax(0, 1fr) 175px; align-items: center; gap: 12px; min-height: 34px; margin: 10px 0 12px; padding: 0 12px; border-left: 2px solid var(--active-color); border-right: 2px solid #8b7cff; background: linear-gradient(90deg, rgba(39, 215, 255, .08), rgba(7, 18, 33, .76) 25% 75%, rgba(139, 124, 255, .08)); font-size: 9px; }
.task-ribbon span { overflow: hidden; color: #6f9fb6; text-overflow: ellipsis; white-space: nowrap; }.task-ribbon span:last-child { text-align: right; }.task-ribbon i { margin-right: 6px; color: var(--active-color); }.task-ribbon strong { overflow: hidden; color: #e3f8ff; font-size: 12px; text-align: center; text-overflow: ellipsis; white-space: nowrap; }

.orchestration-grid { display: grid; grid-template-columns: minmax(230px, 1fr) minmax(340px, 1.42fr) minmax(250px, 1.05fr); gap: 14px; min-height: 376px; }
.stage-rail,.compute-rack,.core-zone { position: relative; min-width: 0; border: 1px solid rgba(62, 140, 176, .2); background: linear-gradient(155deg, rgba(6, 20, 35, .86), rgba(3, 10, 20, .72)); box-shadow: inset 0 0 28px rgba(32, 157, 210, .025); }
.stage-rail,.compute-rack { padding: 12px; }.core-zone { padding: 10px 8px 8px; overflow: hidden; }
.section-title { display: flex; align-items: flex-end; justify-content: space-between; min-height: 31px; margin-bottom: 9px; padding-bottom: 7px; border-bottom: 1px solid rgba(66, 144, 178, .18); }.section-title small { color: #4a7185; font: 7px Consolas, monospace; letter-spacing: .14em; }.section-title strong { color: #cdebf5; font-size: 11px; font-weight: 500; }

.stage-rail > article { position: relative; display: grid; grid-template-columns: 35px minmax(0, 1fr) 18px; align-items: center; gap: 8px; min-height: 57px; margin-top: 6px; padding: 7px 7px; overflow: hidden; border: 1px solid rgba(64, 126, 151, .16); background: rgba(4, 13, 24, .72); opacity: .42; transition: opacity .35s, border-color .35s, transform .35s, background .35s; }
.stage-rail > article::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 2px; background: var(--stage-color); opacity: .25; }
.stage-rail > article.active { border-color: color-mix(in srgb, var(--stage-color) 54%, transparent); background: linear-gradient(90deg, color-mix(in srgb, var(--stage-color) 10%, rgba(4, 13, 24, .92)), rgba(4, 13, 24, .88)); opacity: 1; transform: translateX(3px); box-shadow: 0 0 18px color-mix(in srgb, var(--stage-color) 8%, transparent); }
.stage-rail > article.active::before,.stage-rail > article.complete::before { opacity: 1; box-shadow: 0 0 9px var(--stage-color); }.stage-rail > article.complete { opacity: .72; }.stage-rail > article.error { border-color: #ff6577; opacity: 1; }
.stage-index { position: relative; display: grid; place-items: center; width: 31px; height: 31px; border: 1px solid color-mix(in srgb, var(--stage-color) 42%, transparent); color: var(--stage-color); }.stage-index span { position: absolute; right: 2px; top: 1px; color: #456a7c; font: 6px Consolas, monospace; }.stage-index i { font-size: 12px; }
.stage-copy { min-width: 0; }.stage-copy strong { display: block; overflow: hidden; color: #cce7f0; font-size: 10px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }.stage-copy p { margin: 3px 0 0; overflow: hidden; color: #53798b; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.stage-state { display: grid; place-items: center; color: var(--stage-color); font-size: 8px; }.stage-state span { width: 4px; height: 4px; border-radius: 50%; background: #365566; }.active .stage-state span { background: var(--stage-color); box-shadow: 0 0 8px var(--stage-color); animation: signal-blink .8s step-end infinite; }

.core-heading { position: absolute; z-index: 3; left: 13px; right: 13px; top: 10px; display: grid; grid-template-columns: 1fr auto; align-items: baseline; gap: 2px 12px; }.core-heading small { color: #4a7185; font: 7px Consolas, monospace; letter-spacing: .14em; }.core-heading strong { color: #d9f7ff; font-size: 12px; font-weight: 500; }.core-heading p { grid-column: 1 / -1; overflow: hidden; margin: 0; color: #6a9eb4; font-size: 8px; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.stage-energy { position: absolute; left: 18px; right: 18px; bottom: 10px; display: grid; grid-template-columns: repeat(5, 1fr); gap: 5px; }.stage-energy > span { position: relative; height: 3px; overflow: hidden; background: rgba(81, 128, 151, .14); }.stage-energy i { display: block; height: 100%; background: var(--active-color); box-shadow: 0 0 8px var(--active-color); transition: width .55s cubic-bezier(.2,.75,.2,1); }.stage-energy > span.active:nth-child(2) i { background: #55f991; }.stage-energy > span.active:nth-child(3) i { background: #8b7cff; }.stage-energy > span.active:nth-child(4) i { background: #ffc857; }.stage-energy > span.active:nth-child(5) i { background: #ff6577; }

.radar-panel { display: grid; grid-template-columns: 72px 1fr; align-items: center; gap: 10px; min-height: 82px; padding: 6px 8px; border: 1px solid rgba(39, 215, 255, .16); background: rgba(4, 15, 27, .74); }.radar-panel > div:last-child { display: grid; gap: 2px; }.radar-panel small { color: #4f7d91; font: 7px Consolas, monospace; letter-spacing: .12em; }.radar-panel strong { color: #d8faff; font: 600 26px Bahnschrift, sans-serif; text-shadow: 0 0 14px rgba(39, 215, 255, .36); }.radar-panel p { margin: 0; color: #567d8e; font-size: 8px; }
.vector-radar { position: relative; width: 64px; height: 64px; border: 1px solid rgba(39, 215, 255, .34); border-radius: 50%; background: repeating-radial-gradient(circle, transparent 0 9px, rgba(39, 215, 255, .12) 10px), conic-gradient(from 0deg, rgba(39, 215, 255, .24), transparent 20% 100%); overflow: hidden; }.vector-radar::before,.vector-radar::after { content: ''; position: absolute; background: rgba(39, 215, 255, .18); }.vector-radar::before { left: 50%; top: 0; width: 1px; height: 100%; }.vector-radar::after { left: 0; top: 50%; width: 100%; height: 1px; }.vector-radar > span { position: absolute; inset: 0; background: conic-gradient(from 0deg, rgba(39, 215, 255, .32), transparent 27%); border-radius: 50%; animation: radar-spin 2.8s linear infinite; }
.vector-radar i { --angle: calc((var(--point) - 1) * 51deg); position: absolute; left: calc(50% + 33% * cos(var(--angle))); top: calc(50% + 33% * sin(var(--angle))); width: 3px; height: 3px; border-radius: 50%; background: #55f991; box-shadow: 0 0 7px #55f991; }
.node-list { display: grid; gap: 5px; margin-top: 7px; }.node-list article { display: grid; grid-template-columns: 25px 1fr 9px; align-items: center; gap: 7px; min-height: 38px; padding: 5px 7px; border: 1px solid rgba(64, 126, 151, .13); background: rgba(3, 11, 21, .72); opacity: .42; }.node-list article.online { border-color: color-mix(in srgb, var(--node-accent) 34%, transparent); opacity: 1; }.node-list article > i { color: var(--node-accent); font-size: 11px; text-align: center; }.node-list article > span { display: grid; gap: 2px; }.node-list article small { color: #54798a; font-size: 7px; }.node-list article strong { color: #cbe6ef; font: 9px Consolas, monospace; font-weight: 400; }.node-list article > b { width: 5px; height: 5px; border-radius: 50%; background: #355264; }.node-list article.online > b { background: var(--node-accent); box-shadow: 0 0 8px var(--node-accent); animation: signal-blink .9s step-end infinite; }.node-list article.complete > b { animation: none; }
.quality-panel { margin-top: 7px; padding: 8px; border: 1px solid rgba(139, 124, 255, .18); background: linear-gradient(90deg, rgba(139, 124, 255, .06), rgba(3, 11, 21, .72)); }.quality-panel > div { display: flex; align-items: baseline; justify-content: space-between; }.quality-panel small { color: #6c6f9d; font-size: 7px; }.quality-panel strong { color: #dedaff; font: 600 17px Bahnschrift, sans-serif; }.quality-panel sup { margin-left: 2px; color: #726fa0; font-size: 7px; }.quality-panel > span { display: block; height: 3px; margin: 5px 0; background: rgba(139, 124, 255, .12); }.quality-panel > span i { display: block; height: 100%; background: linear-gradient(90deg, #27d7ff, #8b7cff); box-shadow: 0 0 8px rgba(139, 124, 255, .65); transition: width .6s; }.quality-panel p { margin: 0; color: #515c76; font-size: 7px; }

.console-terminal { display: grid; grid-template-columns: minmax(0, 1fr) 390px; min-height: 81px; margin-top: 10px; border: 1px solid rgba(61, 143, 179, .18); background: rgba(1, 7, 14, .86); }.terminal-heading { grid-column: 1 / -1; display: flex; align-items: center; gap: 8px; height: 20px; padding: 0 9px; border-bottom: 1px solid rgba(61, 143, 179, .13); }.terminal-heading > span { display: flex; gap: 3px; }.terminal-heading > span i { width: 4px; height: 4px; border-radius: 50%; background: #ff6577; }.terminal-heading > span i:nth-child(2) { background: #ffc857; }.terminal-heading > span i:nth-child(3) { background: #55f991; }.terminal-heading strong { color: #527a8d; font: 7px Consolas, monospace; letter-spacing: .14em; }.terminal-heading small { margin-left: auto; color: var(--active-color); font: 7px Consolas, monospace; }
.terminal-lines { display: grid; align-content: center; gap: 3px; padding: 7px 10px; border-right: 1px solid rgba(61, 143, 179, .13); }.terminal-lines code { overflow: hidden; color: #5b8799; font: 8px Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; }.terminal-lines code span { display: inline-block; width: 22px; color: #315365; }.terminal-lines code:last-child { color: #a4dfef; }.terminal-lines b { color: var(--active-color); animation: cursor-blink .7s step-end infinite; }
.telemetry { display: grid; grid-template-columns: repeat(5, 1fr); }.telemetry span { display: grid; place-content: center; min-width: 0; border-left: 1px solid rgba(61, 143, 179, .11); text-align: center; }.telemetry span:first-child { border-left: 0; }.telemetry small { color: #45697b; font-size: 7px; }.telemetry b { margin-top: 3px; color: #c3eef8; font: 600 16px Bahnschrift, sans-serif; }.telemetry em { color: #5d8b9c; font-size: 7px; font-style: normal; }

.master-progress { margin-top: 9px; }.progress-meta { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; color: #527f92; font-size: 8px; }.progress-meta b { color: var(--active-color); font: 10px Consolas, monospace; }.progress-track { position: relative; height: 6px; overflow: visible; border: 1px solid rgba(58, 143, 180, .3); background: repeating-linear-gradient(90deg, rgba(17, 55, 76, .52) 0 18px, transparent 18px 21px); }.progress-track span { display: block; height: 100%; background: linear-gradient(90deg, #27d7ff, var(--active-color)); box-shadow: 0 0 17px color-mix(in srgb, var(--active-color) 76%, transparent); transition: width .6s cubic-bezier(.2,.78,.2,1); }.progress-track i { position: absolute; top: -4px; width: 3px; height: 12px; background: #efffff; box-shadow: 0 0 12px var(--active-color); transition: left .6s cubic-bezier(.2,.78,.2,1); }
.is-paused .progress-track span { animation: hold-pulse 1.6s ease-in-out infinite; }

.error-panel,.success-signal { display: flex; align-items: center; justify-content: space-between; min-height: 39px; margin-top: 9px; padding: 0 11px; border: 1px solid rgba(255, 101, 119, .55); background: rgba(57, 8, 18, .88); color: #ffc3cb; font-size: 10px; }.error-panel button { height: 25px; margin-left: 7px; border: 1px solid #74515a; background: #251117; color: #f9d9dd; cursor: pointer; }.error-panel .retry { border-color: #3ecf80; background: #0b4b2a; color: #d8ffe8; }.success-signal { justify-content: center; gap: 8px; border-color: rgba(85, 249, 145, .48); background: rgba(4, 54, 31, .88); color: #c4ffd8; }
.is-success { --active-color: #55f991; }.is-error { --active-color: #ff6577; }

@keyframes console-scan { from { transform: translateY(-20px); opacity: 0; } 12%,88% { opacity: .5; } to { transform: translateY(640px); opacity: 0; } }
@keyframes radar-spin { to { transform: rotate(360deg); } }
@keyframes signal-blink { 50% { opacity: .24; } }
@keyframes cursor-blink { 50% { opacity: 0; } }
@keyframes hold-pulse { 50% { filter: brightness(1.65); opacity: .68; } }

@media (max-width: 940px) {
  .orchestration-grid { grid-template-columns: 230px 1fr; }.compute-rack { display: none; }
  .console-terminal { grid-template-columns: 1fr; }.telemetry { display: none; }.terminal-lines { border-right: 0; }
}
@media (max-width: 640px) {
  .orchestration-grid { grid-template-columns: 1fr; }
  .stage-rail { display: none; }
  .task-ribbon { grid-template-columns: 1fr; padding: 7px 10px; }
  .task-ribbon span { display: none; }
  .console-header { grid-template-columns: minmax(0, 1fr) auto; }
  .simulation-badge { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .generation-console *,.scan-beam { animation: none !important; transition: none !important; }
}
</style>
