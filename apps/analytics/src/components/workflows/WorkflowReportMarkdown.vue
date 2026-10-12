<script setup lang="ts">
/**
 * WorkflowReportMarkdown —— 渲染后端生成的 Markdown 分析报告（WorkflowRunResult 内使用）。
 *
 * 依赖：marked（解析 GFM 表格）+ DOMPurify（净化）。输出走 v-html，因此净化是唯一的安全边界：
 * 只保留 HTML 白名单标签，并禁止 img / style / iframe / form / input —— 报告内容由模型生成，
 * 不允许引入外部资源加载或表单提交。放宽 FORBID_TAGS 前必须评估 XSS 与隐私外泄风险。
 */
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string }>()
const html = computed(() => DOMPurify.sanitize(marked.parse(props.content, { async: false, gfm: true, breaks: false }), {
  USE_PROFILES: { html: true }, FORBID_TAGS: ['img', 'style', 'iframe', 'form', 'input'],
}))
</script>

<template>
  <article class="workflow-report-markdown" aria-label="分析报告" v-html="html"></article>
</template>

<style scoped>
.workflow-report-markdown { line-height: 1.75; overflow-wrap: anywhere; color: #182230; }
.workflow-report-markdown :deep(h1) { font-size: 24px; margin: 12px 0 20px; }
.workflow-report-markdown :deep(h2) { font-size: 19px; margin: 24px 0 12px; }
.workflow-report-markdown :deep(h3) { font-size: 16px; }
.workflow-report-markdown :deep(table) { display: block; overflow-x: auto; border-collapse: collapse; max-width: 100%; }
.workflow-report-markdown :deep(th), .workflow-report-markdown :deep(td) { padding: 10px 14px; border: 1px solid #e4e7ec; min-width: 90px; }
.workflow-report-markdown :deep(th) { background: #f8fafc; text-align: left; }
.workflow-report-markdown :deep(pre) { white-space: pre-wrap; background: #f8fafc; padding: 12px; }
</style>
