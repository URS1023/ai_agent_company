<script setup lang="ts">
/**
 * 普通检测运行面板（质检工作流的批量执行入口）。
 * 路由：无独立路由；在业务工作流页面/质检入口内嵌使用，状态全部来自 useQualityRun（api/qualityRuns.ts）。
 * 关键交互：选择已发布流程 → 按分组录入样本 → （如有回写）勾选写入确认 → 提交；
 *   提交前/提交中通过 locked 锁定所有输入（busy / 有暂存提交 / 有待恢复提交 / 已有结果）；
 *   存在本地恢复点时给出"恢复并核对 / 放弃恢复信息"的强制二选一（不会自动重发检测）；
 *   离开路由前若提交未核对完成必须二次确认（避免用户误以为已失败而重复运行）。
 * 权限：提交按钮同时要求 can('quality.execute') 与 can('workflows.execute')。
 * 错误呈现：error 就近展示；pendingFailure='unknown' 时明确告知"重试沿用原幂等键，不会创建第二次检测"。
 */
import { computed, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { can } from '../../api/request'
import { useQualityRun } from '../../composables/useQualityRun'
import QualityResourceSelect from './QualityResourceSelect.vue'
import QualityRunSamples from './QualityRunSamples.vue'
import QualityRunResult from './QualityRunResult.vue'
const emit = defineEmits<{ report: [id: string]; completed: [] }>()
const { workflowId, plan, loading, busy, error, result, confirmWrites, groups, pending, pendingRecovery, pendingFailure, restorePending, discardPending, uploads, uploading, count, reload, readResult, submit, nextBatch } = useQualityRun()
// 输入锁定：忙碌、存在暂存提交、存在待恢复提交或已有结果时，禁止再改样本（防止结果与输入不一致）。
const locked = computed(() => busy.value || !!pending.value || !!pendingRecovery.value || !!result.value)
// 结果产出后通知父组件（例如刷新列表或切换页签）。
watch(() => result.value?.execution.id, id => { if (id) emit('completed') })
onBeforeRouteLeave(() => !busy.value && !pending.value || window.confirm('本次检测提交尚未核对完成。离开后请在工作流运行记录核对，勿重复运行。确定离开？'))
</script>
<template>
  <section class="qc-run-panel" aria-label="普通检测运行"><header class="qc-run-intro"><h2>运行检测</h2><p>选择已发布流程，按设备与检验标准添加样本；系统生成每个样本的独立报告。</p></header>
    <QualityResourceSelect v-if="!result" v-model="workflowId" endpoint="/api/v1/workflows" label="已发布检测流程" domain="quality" :statuses="['published']" :disabled="locked || uploading" />
    <p v-if="loading" role="status">正在核对运行方案或读取批次结果…</p><p v-if="error" class="wf-error" role="alert">{{ error }}</p>
    <section v-if="pendingRecovery" class="qc-run-recovery" role="alert" aria-label="未确认的检测提交"><h3>发现未确认的检测提交</h3><p>浏览器中保存了当前质检入口的一次未确认提交。系统不会自动恢复，也不会自动创建第二次检测；请明确选择恢复并核对，或放弃本地恢复信息。</p><div class="wf-actions"><button type="button" class="wf-primary" @click="restorePending">恢复并核对原提交</button><button type="button" @click="discardPending">放弃恢复信息并重新填写</button></div></section>
    <QualityRunResult v-if="result" :result="result" :disabled="loading || busy" @report="emit('report', $event)" @refresh="readResult(workflowId, result.execution.id)" @next="nextBatch" />
    <form v-else-if="plan" id="qc-run-form" @submit.prevent="submit"><p class="qc-run-version">{{ plan.name }} · 发布修订 v{{ plan.publishedRevision }} · 已添加 {{ count }} / 100 个设备样本组合</p>
      <div class="qc-run-groups"><QualityRunSamples v-for="group in plan.groups" :key="group.nodeId" :group="group" v-model="groups[group.nodeId]!" :disabled="locked || uploading" :can-add="count < 100" @uploading="(key, value) => uploads[key] = value" /></div>
      <section v-if="plan.writes.length" class="qc-write-confirm"><h3>确认本次数据库写入</h3><div v-for="write in plan.writes" :key="write.nodeId"><p>{{ write.name }} · {{ write.tableName }} · {{ write.mode === 'insert' ? '追加记录' : '按唯一键更新' }}</p><ul><li v-for="field in write.fields" :key="field.column">{{ field.column }} ← {{ field.path || field.origin }}</li></ul></div><label class="qc-check"><input v-model="confirmWrites" type="checkbox" :disabled="locked" required />我已核对目标表、字段与写入方式，确认执行本次写入</label></section>
      <p v-if="pending && !busy" class="wf-error" role="alert">{{ pendingFailure === 'unknown' ? '上次提交结果尚未确认。输入已锁定；重试会保留原请求和执行键，不会自动创建第二次检测。' : '本次提交已暂存，输入已锁定；请核对原提交。' }}</p><p v-if="busy" role="status">正在执行检测。请勿关闭页面或重复提交；外部设备请求不会自动重试。</p><p v-if="plan.groups.some(group => group.imageRequired)" class="wf-note">外观原图将发送到流程中明确关联的检测服务；需要文件导出权限。报告保留原图哈希与检测来源。</p>
      <div class="wf-actions"><button type="submit" class="wf-primary" :disabled="busy || loading || uploading || !!pendingRecovery || !can('quality.execute') || !can('workflows.execute')">{{ pending ? '核对本次提交' : '开始检测并生成报告' }}</button><button v-if="pending && !busy" type="button" @click="discardPending">已核对运行记录，重新填写</button><button type="button" :disabled="locked || uploading" @click="reload++">重新加载运行方案</button></div>
    </form><p v-else-if="!loading && !result" class="wf-note">无默认流程或模板。流程需发布、包含逐样本质检判定，并将检测节点设为上游样本输入；配置问题会在运行前明确提示。</p>
  </section>
</template>
<style scoped>
.qc-run-groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:18px;align-items:start}
.qc-run-recovery{margin:16px 0;padding:16px;border:1px solid #d8a53d;border-radius:6px;background:#fffaf0}.qc-run-recovery h3{margin:0 0 8px;font-size:16px}.qc-run-recovery p{margin:0 0 12px;line-height:1.6;color:#5b4a23}
.qc-run-panel{border:1px solid var(--qc-line);background:var(--qc-paper);padding:22px;border-radius:6px;margin-bottom:22px;min-width:0}.qc-run-intro h2{font-size:20px;margin:0 0 10px}.qc-run-intro p{color:var(--qc-muted);line-height:1.7}.qc-run-version{padding:12px;background:var(--qc-back);border-left:3px solid var(--qc-blue);line-height:1.8}.qc-write-confirm{border:1px solid #b9974e;padding:18px;margin:18px 0}@media(max-width:650px){.qc-run-panel{padding:14px}.qc-write-confirm{padding:12px}}
</style>
