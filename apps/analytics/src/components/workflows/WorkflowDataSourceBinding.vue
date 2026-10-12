<script setup lang="ts">
/**
 * WorkflowDataSourceBinding —— 节点级数据源绑定（http-request / database-query / report-analysis 共用）。
 *
 * 位置：WorkflowNodeForm 内按节点 kind 渲染。
 * 依赖接口：`api/request.can`（新建 HTTP 数据源的权限）、`api/collection` 的 httpCollection / httpRequestCount /
 * CollectionResult / HttpSource、HttpCollectionResult（逐条采集结果）、WorkflowHttpSourceDialog（就地新建/编辑）。
 * 关键约束：
 * - 候选数据源按节点类型过滤：http-request 只能选 http，其他节点排除 http，
 *   禁止跨类型绑定（运行时会直接失败）；
 * - 绑定必须同时写入 datasourceId 与 datasourceVersion：版本号用于锁定配置快照，
 *   只存 ID 会让节点在数据源被改动后静默换语义；
 * - 测试采集是真实外部请求：必须先经用户确认（提示请求次数、POST 可能的业务副作用），
 *   失败只展示错误不自动重试；测试不改变绑定关系。
 */
import { computed, shallowRef } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { can } from '../../api/request'
import { httpCollection, httpRequestCount, type CollectionResult, type HttpSource } from '../../api/collection'
import HttpCollectionResult from '../collection/HttpCollectionResult.vue'
import WorkflowHttpSourceDialog from './WorkflowHttpSourceDialog.vue'

const props = defineProps<{
  kind: string
  config: Record<string, any>
  sources: any[]
  disabled: boolean
}>()
const emit = defineEmits<{
  update: [config: Record<string, any>]
  sourceSaved: [source: any]
}>()
const sourceDialog = shallowRef(false)
const editing = shallowRef<HttpSource | null>(null)
const testing = shallowRef(false)
const testError = shallowRef('')
const result = shallowRef<CollectionResult | null>(null)
const resultOpen = shallowRef(false)

const isHttp = computed(() => props.kind === 'http-request')
/** 候选数据源：按节点类型互斥过滤，避免把 HTTP 源绑到数据库节点上。 */
const compatibleSources = computed(() => props.sources.filter(source => isHttp.value ? source.type === 'http' : source.type !== 'http'))
const selected = computed(() => compatibleSources.value.find(source => source.id === props.config.datasourceId))
const sourceTypeName = computed(() => isHttp.value ? 'HTTP 数据源' : props.kind === 'report-analysis' ? '分析数据源' : '数据库数据源')

/**
 * 切换绑定：同时写入 datasourceVersion（找不到源时为 null，表示需重新选择），
 * 保证节点配置锁定的是选定时刻的数据源版本快照。
 */
function selectSource(event: Event) {
  const id = (event.target as HTMLSelectElement).value
  const source = compatibleSources.value.find(item => item.id === id)
  emit('update', {
    ...props.config,
    datasourceId: id,
    datasourceVersion: source?.version ?? null,
  })
}

function createHttpSource() {
  editing.value = null
  sourceDialog.value = true
}

function editHttpSource() {
  if (!selected.value || selected.value.type !== 'http') return
  editing.value = selected.value as HttpSource
  sourceDialog.value = true
}

function saved(source: HttpSource) {
  emit('sourceSaved', source)
  emit('update', {
    ...props.config,
    datasourceId: source.id,
    datasourceVersion: source.version,
  })
}

/**
 * 测试当前 HTTP 数据源：先弹确认框（含请求次数与 POST 副作用提示），确认后才发起真实采集。
 * 用户取消不视为错误（ElMessageBox 的 cancel 走 catch 分支，此处仅在真实 Error 时展示错误）。
 */
async function testHttpSource() {
  if (!selected.value || selected.value.type !== 'http') return
  const source = selected.value as HttpSource
  testError.value = ''
  testing.value = true
  try {
    await ElMessageBox.confirm(
      `${source.method} ${source.url}，配置 v${source.version}，共 ${httpRequestCount(source)} 次请求。${source.method === 'POST' ? 'POST 可能产生业务副作用，请确认该接口和请求体可用于本次测试。' : '本次操作会读取接口真实数据。'}`,
      '测试当前 HTTP 数据源',
      { type: 'warning', confirmButtonText: '执行一次测试', cancelButtonText: '取消' },
    )
    const response = await httpCollection.collect(source)
    result.value = response.data
    resultOpen.value = true
    if (response.data.status === 'success') ElMessage.success(`采集成功，共返回 ${response.data.totalCount} 行`)
    else ElMessage.warning(response.message || '采集未完全成功，请查看逐条结果')
  } catch (cause) {
    if (cause instanceof Error) testError.value = cause.message
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <section class="wf-source-binding" :aria-label="sourceTypeName + '绑定'">
    <label>{{ sourceTypeName }}
      <select :value="config.datasourceId" :disabled="disabled" @change="selectSource">
        <option value="">请选择当前账号有权使用的数据源</option>
        <option v-for="source in compatibleSources" :key="source.id" :value="source.id">
          {{ source.name }} · {{ String(source.type).toUpperCase() }}{{ source.enabled ? '' : '（停用）' }}
        </option>
      </select>
    </label>

    <article v-if="selected" class="wf-source-summary" :class="{ warning: !selected.enabled || (isHttp && !selected.allowAutomatic) }">
      <header><strong>{{ selected.name }}</strong><span>v{{ selected.version }}</span></header>
      <template v-if="isHttp">
        <p><b>{{ selected.method }}</b><code>{{ selected.url }}</code></p>
        <dl>
          <div><dt>请求</dt><dd>{{ httpRequestCount(selected) }} 次</dd></div>
          <div><dt>响应格式</dt><dd>{{ selected.responseFormat === 'text' ? '网页 / 文本' : 'JSON' }}</dd></div>
          <div v-if="selected.responseFormat !== 'text'"><dt>记录路径</dt><dd><code>{{ selected.recordPath || '$' }}</code></dd></div>
          <div><dt>字段</dt><dd>{{ selected.responseFormat === 'text' ? '原文、内容类型和页面地址' : selected.fields?.length ? selected.fields.length + ' 个' : '保留全部' }}</dd></div>
        </dl>
        <p class="wf-source-state">{{ selected.enabled ? selected.allowAutomatic ? '已启用，可供工作流自动只读采集' : '尚未授权自动采集，请先编辑并确认用途' : '数据源已停用，不能运行' }}</p>
      </template>
      <template v-else>
        <p>{{ selected.environmentSummary || `${String(selected.type).toUpperCase()} · ${selected.databaseName || '未命名数据库'}` }}</p>
        <p>{{ selected.description || '未填写业务口径说明' }}</p>
        <dl>
          <div><dt>元数据</dt><dd>{{ selected.metadataStatus || 'not_synced' }}</dd></div>
          <div><dt>业务表</dt><dd>{{ selected.businessTableCount ?? selected.tableCount ?? '—' }}</dd></div>
          <div><dt>语义索引</dt><dd>{{ selected.semanticIndexStatus || 'not_indexed' }}</dd></div>
        </dl>
      </template>
    </article>
    <p v-else class="wf-note">没有默认选择。AI 和运行引擎只会使用您明确绑定的授权资源。</p>

    <p v-if="testError" class="wf-error" role="alert">{{ testError }}</p>
    <div class="wf-source-actions">
      <template v-if="isHttp">
        <button v-if="can('datasources.create')" type="button" :disabled="disabled || testing" @click="createHttpSource">新建 HTTP 数据源</button>
        <button v-if="can('datasources.edit') && selected" type="button" :disabled="disabled || testing" @click="editHttpSource">编辑请求配置</button>
        <button v-if="can('datasources.execute') && selected" type="button" :disabled="disabled || testing || !selected.enabled" @click="testHttpSource">{{ testing ? '正在测试…' : '测试采集' }}</button>
      </template>
      <RouterLink class="wf-source-link" :to="isHttp ? { path: '/datasources', query: { type: 'http' } } : '/datasources'">打开数据源管理</RouterLink>
    </div>

    <WorkflowHttpSourceDialog v-model="sourceDialog" :source="editing" @saved="saved" />
    <el-dialog v-model="resultOpen" title="HTTP 测试采集结果" width="min(1120px, 96vw)" append-to-body destroy-on-close>
      <HttpCollectionResult v-if="result" :key="result.queryId" :result="result" />
    </el-dialog>
  </section>
</template>
