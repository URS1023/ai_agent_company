<script setup lang="ts">
/**
 * 应用根组件（挂在 #analytics-app 下）。
 * 职责：渲染路由出口，并捕获子树未处理错误，避免单个业务页面崩溃导致整屏白屏。
 * 关键交互：捕获到错误时用 role="alert" 提示条展示 message，并提供"重试当前模块"按钮清空错误。
 * 约束：onErrorCaptured 返回 false 表示错误已被处理、不再向上冒泡；此处只做展示，不上报、不重试请求。
 */
import { onErrorCaptured, ref } from 'vue'
const error = ref('')
onErrorCaptured((cause) => { error.value = cause.message; return false })
</script>
<template>
  <div class="analytics-app">
    <section v-if="error" class="business-page" role="alert"><h1>模块加载失败</h1><p>{{ error }}</p><el-button @click="error = ''">重试当前模块</el-button></section>
    <RouterView v-else />
  </div>
</template>
