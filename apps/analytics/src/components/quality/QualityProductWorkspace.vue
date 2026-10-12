<script setup lang="ts">
/**
 * 质检产品工作台（路由 /quality 的主页面容器，由 QualityInspectionWorkspace 渲染）。
 * 职责：按产品分组展示检测要求卡片（含图片、检测方案切换、详情与运行记录入口），并聚合"待确认归属/尚未关联要求"
 *   两类缺口提示、让步接收面板、历史设备检测入口与旧版报告弹窗。
 * 依赖接口：useQualityProducts（api/qualityProducts.ts 目录快照）+ 目录图片 api/catalogImages.ts（上传后 load 刷新）。
 * 关键交互：卡片可切换同一产品的多套检测方案（本地选择，不落库）；打开工作流/运行记录会带 from=quality 与搜索词跳转；
 *   工作区 URL 带 ?report= 或 ?inspection= 时自动弹出"质检运行结果与报告"（支持深链分享）。
 * 懒加载：BusinessDeviceWorkspace / BusinessResultsPanel 使用 defineAsyncComponent，且仅在弹窗打开时挂载，
 *   避免首屏加载设备与报告模块的重依赖。
 * 权限：让步接收面板与设备检测入口分别受 can('quality.view') / can('devices.view') 控制。
 * 错误呈现：error 时显示重试按钮；空态区分"搜索无结果"与"尚未配置产品工作流"。
 */
import { computed, defineAsyncComponent, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { can } from '../../api/request'
import { qualityCategories, type QualityProductGroup } from '../../api/qualityProducts'
import { useQualityProducts } from '../../composables/useQualityProducts'
import QualityProductCard from './QualityProductCard.vue'
import QualityProductDetails from './QualityProductDetails.vue'
import QualityCatalogueNotice from './QualityCatalogueNotice.vue'
import CatalogPagination from '../business/CatalogPagination.vue'
import BusinessManagementLinks from '../business/BusinessManagementLinks.vue'
import CatalogImageDialog from '../business/CatalogImageDialog.vue'
import QualityConcessionPanel from './QualityConcessionPanel.vue'
import type { ImageTarget } from '../../api/catalogImages'
import '../../styles/workflows.css'
import '../../styles/quality-products.css'
import '../../styles/business-catalog.css'

const BusinessDeviceWorkspace = defineAsyncComponent(() => import('../business/BusinessDeviceWorkspace.vue'))
const BusinessResultsPanel = defineAsyncComponent(() => import('../business/BusinessResultsPanel.vue'))
const route = useRoute()
const legacyOpen = shallowRef(false), reportsOpen = shallowRef(false)
const imageTarget = shallowRef<ImageTarget | null>(null), details = shallowRef<QualityProductGroup | null>(null)
const { catalogue, loading, error, keyword, groups, pagedGroups, page, pageSize, changeSize, currentEntries, unassociated, unassociatedCount, unconfigured, load, open } = useQualityProducts()
const productCount = computed(() => new Set(currentEntries.value.map(entry => entry.productId).filter(Boolean)).size)
const requirementCount = computed(() => currentEntries.value.filter(entry => entry.productId).length)
// 深链支持：URL 带 report/inspection 查询参数时直接打开"质检运行结果与报告"弹窗（历史分享链接可用）。
watch(() => [route.query.report, route.query.inspection], ([report, inspection]) => { if (report || inspection) reportsOpen.value = true }, { immediate: true })
</script>
<template>
  <main class="quality-products workflow-ui">
    <header class="quality-heading"><div><span>QUALITY INSPECTION</span><h1>质量检查</h1><p>直接选择产品的检测要求，打开工作流。同一产品的检测要求集中在一起。</p></div><div class="wf-actions"><button type="button" @click="reportsOpen = true">查看质检结果</button><button type="button" :disabled="loading" @click="load">刷新</button></div></header>
    <BusinessManagementLinks domain="quality" />
    <p v-if="loading" role="status" class="quality-feedback">正在读取产品与检测要求…</p>
    <div v-else-if="error" class="quality-feedback wf-error" role="alert"><p>{{ error }}</p><button type="button" @click="load">重试</button></div>
    <template v-else-if="catalogue">
      <QualityCatalogueNotice :catalogue="catalogue" />
      <div class="quality-product-toolbar"><p><strong>{{ productCount }}</strong> 个产品 · {{ requirementCount }} 套检测要求</p><label><span class="sr-only">搜索产品或检测要求</span><input v-model="keyword" type="search" placeholder="搜索产品、编码或检测要求" aria-label="搜索产品或检测要求" /></label></div>
      <div v-if="groups.length" class="catalog-grid"><QualityProductCard v-for="product in pagedGroups" :key="product.id" :product="product" @open="open" @details="details = product" @image="imageTarget = { kind: 'product', id: product.id, name: product.name, image: product.image }" /></div>
      <div v-else class="quality-feedback"><h2>{{ keyword ? '没有匹配的产品或检测要求' : '尚未配置产品工作流' }}</h2><p>{{ keyword ? '可尝试产品名称、编码或清空搜索。' : '已配置并授权的产品工作流会显示在这里。' }}</p><button v-if="keyword" type="button" @click="keyword = ''">清空搜索</button></div>
      <details v-if="unassociated.length" class="quality-unconfigured"><summary>{{ unassociatedCount }} 张历史单据待确认产品归属</summary><p>源单据未关联产品模板，保留数据并标记待确认，不混入任何产品。</p><div class="quality-unassociated-links"><button v-for="entry in unassociated" :key="entry.workflowId" type="button" @click="open(entry)">{{ qualityCategories[entry.category] }} · {{ entry.formCount }} 张单据</button></div></details>
      <details v-if="unconfigured.length" class="quality-unconfigured"><summary>原库中另有 {{ unconfigured.length }} 个产品尚未关联任何质检要求</summary><p>这些产品尚无明确的质检类别和模板，未生成自动判断流程。需要先确认是否需要质检及其检测要求。</p><ul><li v-for="product in unconfigured" :key="product.id"><strong>{{ product.name }}</strong><span>{{ product.code }}</span></li></ul></details>
    <p v-if="catalogue.sourceLabel" class="quality-source-note">{{ catalogue.sourceLabel }} · 目录与单据数量核对于 {{ catalogue.snapshotAt }}，属于配置快照；运行工作流时重新读取源库数据与单据标准。</p>
    <CatalogPagination :total="groups.length" :page="page" :page-size="pageSize" :disabled="loading" @page="page = $event" @size="changeSize" />
    </template>
    <QualityConcessionPanel v-if="can('quality.view')" />
    <CatalogImageDialog :target="imageTarget" @close="imageTarget = null" @saved="load()" />
    <QualityProductDetails :product="details" @close="details = null" @open="open" />
    <details class="quality-legacy"><summary>其他检测入口</summary><button v-if="can('devices.view')" type="button" @click="legacyOpen = true">设备图像与成分检测</button><button type="button" @click="reportsOpen = true">历史设备检测报告</button></details>
    <el-dialog v-model="legacyOpen" title="设备图像与成分检测" width="min(1280px, 96vw)" destroy-on-close><BusinessDeviceWorkspace v-if="legacyOpen" domain="quality" /></el-dialog>
    <el-dialog v-model="reportsOpen" title="质检运行结果与报告" width="min(1280px, 96vw)" destroy-on-close><BusinessResultsPanel v-if="reportsOpen" domain="quality" /></el-dialog>
  </main>
</template>
