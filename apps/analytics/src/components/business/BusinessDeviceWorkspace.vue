<script setup lang="ts">
/**
 * 业务设备工作台（路由 /alerts 与 /quality 的主页面，由 AlertWorkspace / QualityInspectionWorkspace 传入 domain）。
 * 职责：设备目录卡片（搜索、分页、图片）、设备设置与新增弹窗、报告与问题结果面板、以及跳转设备工作流。
 * 依赖接口：useBusinessDevices（api/alerts.ts 的 device-catalogue + api/request.ts）、目录图片 api/catalogImages.ts、表单复用 EquipmentForm。
 * 关键交互：URL 带 ?report=/?inspection= 时自动打开结果面板（支持深链）；设备设置未保存时离开路由需确认；
 *   新增/编辑/删除/打开工作流按钮分别受 devices.create/edit/delete 与 workflows.view 控制。
 * 约束：domain 决定文案与操作域（alerts / quality），切换域时 composable 会恢复各自的列表记忆状态。
 * 错误呈现：error 由选择器组件内联展示，弹窗内的错误就近展示。
 */
import { computed, shallowRef, toRef, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { can } from '../../api/request'
import type { BusinessDeviceDomain } from '../../api/deviceWorkflows'
import { useBusinessDevices } from '../../composables/useBusinessDevices'
import EquipmentForm from '../alerts/EquipmentForm.vue'
import BusinessDeviceSelector from './BusinessDeviceSelector.vue'
import BusinessManagementLinks from './BusinessManagementLinks.vue'
import BusinessResultsPanel from './BusinessResultsPanel.vue'
import CatalogPagination from './CatalogPagination.vue'
import CatalogImageDialog from './CatalogImageDialog.vue'
import type { ImageTarget } from '../../api/catalogImages'
import '../../styles/business-devices.css'
import '../../styles/workflows.css'
import '../../styles/alerts.css'
import '../../styles/business-catalog.css'

const props = defineProps<{ domain: BusinessDeviceDomain }>()
const route = useRoute()
const resultsOpen = shallowRef(false)
const imageTarget = shallowRef<ImageTarget | null>(null)
// 深链支持：?report= 或 ?inspection= 直接展开结果面板（从工作流/报告页跳回时可定位）。
watch(() => [route.query.report, route.query.inspection], ([report, inspection]) => {
  if (report || inspection) resultsOpen.value = true
}, { immediate: true })
const {
  rows, sources, form, loading, total, page, pageSize, busy, error, keyword, formOpen,
  load, changePage, changeSize, search, edit, save, remove, openWorkflow, closeForm,
} = useBusinessDevices(toRef(props, 'domain'))
const labels = computed(() => props.domain === 'alert' ? {
  title: '设备与预警',
  summary: '选择设备后直接配置、发布和运行预警工作流。测点、判断条件与通知都在流程节点中完成。',
  operationDomain: 'alerts',
} : {
  title: '质量检查',
  summary: '选择设备后直接配置、发布和运行质检工作流。检测标准、数据来源与复核规则都在流程节点中完成。',
  operationDomain: 'quality',
})
/** 保存权限：已有 id 走 devices.edit，否则 devices.create（无权限时表单整体禁用）。 */
const canSave = computed(() => can('devices.' + (form.value?.id ? 'edit' : 'create')))
onBeforeRouteLeave(() => !formOpen.value || window.confirm('设备设置尚未保存，确定离开此页面？'))
</script>

<template>
  <div class="business-page workflow-ui device-entry-page" :data-domain="domain">
    <header class="device-entry-heading">
      <div>
        <h1>{{ labels.title }}</h1>
        <p>{{ labels.summary }}</p>
      </div>
      <div class="device-entry-actions">
        <button v-if="can(domain === 'alert' ? 'alerts.view' : 'quality.view')" type="button" @click="resultsOpen = true">{{ domain === 'alert' ? '查看报告与问题' : '查看质检报告' }}</button>
        <button v-if="can('devices.create')" type="button" class="wf-primary" :disabled="busy" @click="edit()">
          <i class="fa-solid fa-plus" aria-hidden="true" />新增设备
        </button>
      </div>
    </header>
    <BusinessManagementLinks :domain="domain" />
    <BusinessDeviceSelector
      v-model:keyword="keyword"
      :domain="domain"
      :rows="rows"
      :loading="loading"
      :busy="busy"
      :error="error"
      :can-open="can('workflows.view')"
      :can-edit="can('devices.edit')"
      :can-create="can('devices.create')"
      :can-remove="can('devices.delete')"
      @search="search"
      @refresh="load()"
      @open="openWorkflow"
      @edit="edit"
      @create="edit()"
      @remove="remove"
      @image="device => imageTarget = { kind: 'device', id: device.id || '', name: device.name, image: device.image }"
    />
    <CatalogPagination :total="total" :page="page" :page-size="pageSize" :disabled="loading || busy" @page="changePage" @size="changeSize" />
    <CatalogImageDialog :target="imageTarget" @close="imageTarget = null" @saved="load()" />

    <el-dialog v-model="resultsOpen" :title="domain === 'alert' ? '预警报告与问题处置' : '质检报告与复核'" width="min(1280px, 96vw)" destroy-on-close>
      <BusinessResultsPanel v-if="resultsOpen" :domain="domain" />
    </el-dialog>
    <el-dialog
      v-model="formOpen"
      :title="form?.id ? '设备设置' : '新增设备'"
      width="min(880px, 96vw)"
      :close-on-click-modal="false"
      :before-close="closeForm"
      destroy-on-close
    >
      <form v-if="form" id="business-device-form" class="workflow-ui alerts-ui" @submit.prevent="save">
        <EquipmentForm v-model="form" kind="devices" :devices="[]" :sources="sources" :disabled="busy || !canSave" />
        <p v-if="error" class="wf-error" role="alert">{{ error }}</p>
      </form>
      <template #footer>
        <button type="button" :disabled="busy" @click="closeForm(() => formOpen = false)">取消</button>
        <button v-if="canSave" type="submit" form="business-device-form" class="wf-primary" :disabled="busy">保存设备</button>
      </template>
    </el-dialog>
  </div>
</template>
