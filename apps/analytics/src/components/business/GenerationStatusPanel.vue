<script setup lang="ts">
/**
 * 生成任务状态面板（运行中 / 成功 / 失败 + 进度事件流水）。
 * 路由：无独立路由；供大屏与报表 AI 生成流程展示服务端进度记录（record.events）。
 * 依赖接口：无请求（状态由父组件轮询/推送后传入）；back/retry 事件由父组件决定返回或重试。
 * 约束：state==='running' 时用不确定进度条（indeterminate）且不显示百分比，禁止展示模拟完成率；
 *   失败态才提供"重新执行"，避免用户在生成中重复触发。
 */
defineProps<{
  state: string
  error: string
  datasourceName: string
  record?: { id?: string; events?: Array<{ phase: string; message: string; time: string }> } | null
}>()
defineEmits<{ back: []; retry: [] }>()
</script>
<template>
  <section class="generation-status" aria-live="polite" :aria-busy="state === 'running'">
    <h2>{{ state === 'running' ? '正在处理生成请求' : state === 'success' ? '生成处理完成' : '生成未完成' }}</h2>
    <p>数据范围：{{ datasourceName }}。只有通过数据查询校验的组件才会写入画布。</p>
    <el-progress v-if="state === 'running'" :percentage="100" indeterminate :show-text="false"
      aria-label="报表生成进度" :aria-valuenow="null" aria-valuetext="正在处理生成请求" />
    <p v-if="state === 'running'">服务端正在读取元数据、生成布局并验证查询。当前等待结果，不显示模拟完成率。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <ol v-if="record?.events?.length"><li v-for="(event, index) in record.events" :key="index"><time>{{ event.time }}</time><span>{{ event.message }}</span></li></ol>
    <p v-if="record?.id">生成记录：<code>{{ record.id }}</code></p>
    <div v-if="state !== 'running'" class="form-row"><el-button @click="$emit('back')">返回修改</el-button><el-button v-if="state === 'error'" type="primary" @click="$emit('retry')">重新执行</el-button></div>
  </section>
</template>
<style scoped>
.generation-status{padding:36px;background:#f4f7fb;color:#172b43;min-height:260px;border-radius:8px}.generation-status h2{font-size:22px}.generation-status p{line-height:1.9;color:#52647a}.generation-status li{display:flex;gap:16px;padding:10px 0;border-bottom:1px solid #dce4ed}.generation-status time{font-size:12px;color:#61758a}.generation-status code{overflow-wrap:anywhere}
</style>
