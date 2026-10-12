<script setup lang="ts">
/**
 * 质量检查工作区页面（路由 /quality，权限 quality.view）。
 * 质量检查与设备预警使用同一套业务工作台：先选择业务对象，再打开对应的工作流，
 * 并从同一入口查看运行报告、问题和复核记录。页面本身只负责传入 quality 业务域。
 */
import BusinessDeviceWorkspace from '../components/business/BusinessDeviceWorkspace.vue'
</script>
<template><BusinessDeviceWorkspace domain="quality" /></template>
