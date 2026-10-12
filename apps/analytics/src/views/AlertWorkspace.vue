<script setup lang="ts">
/**
 * 设备预警工作区页面（路由 /alerts，权限 alerts.view + devices.view）。
 * 职责：薄壳页面，把 domain="alert" 交给设备工作台组件；设备目录、设置弹窗与工作流入口均由后者承担。
 * 说明：本页不发起请求、不持有状态，空态与错误态由 BusinessDeviceWorkspace 呈现。
 */
import BusinessDeviceWorkspace from '../components/business/BusinessDeviceWorkspace.vue'
</script>
<template><BusinessDeviceWorkspace domain="alert" /></template>
