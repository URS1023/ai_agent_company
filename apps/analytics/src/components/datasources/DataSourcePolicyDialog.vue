<script setup lang="ts">
/**
 * 数据源治理策略弹窗（并发上限、每日查询额度、健康检查调度）+ 近期健康检查历史。
 * 路由：无独立路由，由 DataSourceManage 在 /datasources 内以 v-model:open 控制显隐。
 * 依赖接口：GET/PUT /api/v1/analytics/datasources/{id}/policy 与 .../health-checks。
 * 关键交互：打开时并发拉取策略与最近 20 条健康检查；保存后回写 version（乐观锁）并 emit('saved') 让列表刷新。
 * 约束：canManageSchedule 为 false 时不允许修改定时检查相关项（调度任务属系统运营权限）；
 *   每日查询上限按 UTC 自然日统计、0 表示不限（界面文案已说明），改动会影响所有使用该数据源的报表。
 * 错误呈现：加载/保存失败均用 ElMessage.error 展示后端 message。
 */
import { reactive, shallowRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { get, put } from '../../api/request'
import type { DataSourceHealth, DataSourcePolicy, DataSourceSummary } from './types'

type Envelope<T> = { data: T }

const props = defineProps<{
  source: DataSourceSummary | null
  canManageSchedule: boolean
}>()
const emit = defineEmits<{ saved: [] }>()
const open = defineModel<boolean>({ default: false })
const loading = shallowRef(false)
const saving = shallowRef(false)
const policy = shallowRef<DataSourcePolicy | null>(null)
const history = shallowRef<DataSourceHealth[]>([])
const form = reactive({
  version: 0,
  maxConcurrency: 4,
  dailyQueryLimit: 10000,
  healthCheckEnabled: false,
  healthIntervalMinutes: 60,
})

async function load() {
  if (!props.source) return
  loading.value = true
  try {
    const [policyResponse, historyResponse] = await Promise.all([
      get<Envelope<DataSourcePolicy>>(`/api/v1/analytics/datasources/${props.source.id}/policy`),
      get<Envelope<DataSourceHealth[]>>(`/api/v1/analytics/datasources/${props.source.id}/health-checks`, { limit: 20 }),
    ])
    policy.value = policyResponse.data
    history.value = historyResponse.data
    Object.assign(form, {
      version: policyResponse.data.version,
      maxConcurrency: policyResponse.data.maxConcurrency,
      dailyQueryLimit: policyResponse.data.dailyQueryLimit,
      healthCheckEnabled: policyResponse.data.healthCheckEnabled,
      healthIntervalMinutes: policyResponse.data.healthIntervalMinutes,
    })
  } catch (caught) {
    ElMessage.error('加载治理策略失败：' + (caught as Error).message)
  } finally {
    loading.value = false
  }
}

watch([open, () => props.source?.id], ([visible]) => {
  if (visible) void load()
}, { immediate: true })

/** 保存策略：PUT /policy，成功后用返回值刷新 version（避免后续保存因版本过期失败）。 */
async function save() {
  if (!props.source) return
  saving.value = true
  try {
    const response = await put<Envelope<DataSourcePolicy>>(
      `/api/v1/analytics/datasources/${props.source.id}/policy`,
      { ...form },
    )
    policy.value = response.data
    form.version = response.data.version
    emit('saved')
    ElMessage.success('数据源治理策略已保存')
  } catch (caught) {
    ElMessage.error('保存失败：' + (caught as Error).message)
  } finally {
    saving.value = false
  }
}

function formatTime(value: string) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value.endsWith('Z') ? value : value + 'Z'))
}
</script>

<template>
  <el-dialog v-model="open" :title="source ? `${source.name} · 治理策略` : '数据源治理策略'" width="min(760px, 96vw)" :close-on-click-modal="false">
    <div v-loading="loading" class="policy-content">
      <dl v-if="policy" class="runtime-facts">
        <div><dt>活跃查询</dt><dd>{{ policy.activeQueries }} / {{ policy.maxConcurrency }}</dd></div>
        <div><dt>今日查询（UTC）</dt><dd>{{ policy.queriesToday }}{{ policy.dailyQueryLimit ? ` / ${policy.dailyQueryLimit}` : ' / 不限' }}</dd></div>
        <div><dt>调度状态</dt><dd>{{ policy.scheduleStatus === 'active' ? '运行中' : policy.scheduleStatus === 'paused' ? '已暂停' : '未配置' }}</dd></div>
        <div><dt>下次检查</dt><dd>{{ formatTime(policy.nextHealthCheckAt) }}</dd></div>
      </dl>

      <el-form class="policy-form" label-position="top">
        <div class="policy-grid">
          <el-form-item label="并发查询上限">
            <el-input-number v-model="form.maxConcurrency" :min="1" :max="32" />
          </el-form-item>
          <el-form-item label="每日查询上限（UTC）">
            <el-input-number v-model="form.dailyQueryLimit" :min="0" :max="1000000" />
            <small>0 表示不限；UTC 00:00 重置（北京时间 08:00）。</small>
          </el-form-item>
          <el-form-item label="自动健康检查">
            <el-switch v-model="form.healthCheckEnabled" :disabled="!canManageSchedule && !policy?.healthCheckEnabled" />
          </el-form-item>
          <el-form-item label="检查间隔">
            <el-input-number v-model="form.healthIntervalMinutes" :min="5" :max="10080" :disabled="!form.healthCheckEnabled" />
            <small>分钟</small>
          </el-form-item>
        </div>
        <p v-if="!canManageSchedule" class="permission-note">启用自动检查需要系统运维管理权限；已有调度仍可在此停用。</p>
      </el-form>

      <section class="health-history">
        <h3>健康检查历史</h3>
        <el-table :data="history" size="small" max-height="250" empty-text="尚未执行连接检查">
          <el-table-column prop="checkedAt" label="检查时间" width="150">
            <template #default="scope">{{ formatTime(scope.row.checkedAt) }}</template>
          </el-table-column>
          <el-table-column prop="triggerType" label="来源" width="88">
            <template #default="scope">{{ scope.row.triggerType === 'schedule' ? '自动' : '手动' }}</template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="88">
            <template #default="scope"><span class="check-state" :class="scope.row.status">{{ scope.row.status === 'healthy' ? '正常' : '异常' }}</span></template>
          </el-table-column>
          <el-table-column prop="latencyMs" label="耗时" width="80">
            <template #default="scope">{{ scope.row.latencyMs }} ms</template>
          </el-table-column>
          <el-table-column prop="detail" label="结果" min-width="220" show-overflow-tooltip />
        </el-table>
      </section>
    </div>
    <template #footer>
      <el-button @click="open = false">关闭</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存策略</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.policy-content { min-height: 330px; }
.runtime-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0 0 18px; border: 1px solid #dfe6ee; background: #f8fafc; }
.runtime-facts div { min-width: 0; padding: 12px 14px; border-right: 1px solid #e1e7ee; }
.runtime-facts div:last-child { border-right: 0; }
.runtime-facts dt { color: #718195; font-size: 10px; }
.runtime-facts dd { margin: 5px 0 0; overflow: hidden; color: #153b61; font-size: 13px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.policy-form { padding: 2px 4px 8px; border-bottom: 1px solid #e5eaf0; }
.policy-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 24px; }
.policy-grid small { margin-left: 8px; color: #718195; }
.permission-note { margin: -2px 0 8px; color: #8a6430; font-size: 11px; }
.health-history h3 { margin: 17px 0 10px; color: #163956; font-size: 13px; }
.check-state { display: inline-flex; padding: 2px 6px; color: #9a3e38; background: #f8e9e8; border-radius: 3px; font-size: 10px; }
.check-state.healthy { color: #21684e; background: #e6f3ed; }
@media (max-width: 700px) {
  .runtime-facts, .policy-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .runtime-facts div:nth-child(2) { border-right: 0; }
}
</style>
