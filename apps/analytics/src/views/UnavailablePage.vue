<!--
  无权限 / 路径不存在页（router.ts 的兜底路由与权限拦截都会跳到 /unavailable）。
  职责：以 el-result 展示原因，文案取 route.query.reason（由守卫写入的中文原因）；
  没有 reason 时回退为"路径不存在"的通用说明。本页无状态、无请求、无权限要求。
-->
<template><main class="business-page"><el-result icon="warning" title="页面不可用" :sub-title="String($route.query.reason || '该模块路径不存在，请从平台菜单选择。')" /></main></template>
