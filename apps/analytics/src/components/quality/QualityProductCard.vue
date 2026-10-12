<script setup lang="ts">
/**
 * 质检产品卡片（产品名 + 状态 + 检测方案 + 检测要求统计 + 图片 + 操作按钮）。
 * 路由：无独立路由，由 QualityProductWorkspace 在产品网格中渲染。
 * 依赖接口：无请求；数据来自 useQualityProducts 的目录分组，图片编辑按钮受 can('quality.edit') 与文件权限控制。
 * 关键交互：同一产品有多套检测方案时以 select 切换（chosen 为本地选择，默认取第一套）；
 *   open 事件带 logs=true 表示直接进入运行记录；无检测方案时两个入口按钮禁用。
 * 约束：状态徽标根据 status/publishedRevision 推导（已停用/已发布/未发布），与后端发布语义一致。
 */
import { computed, shallowRef } from 'vue'
import { qualityCategories, type QualityProductEntry, type QualityProductGroup } from '../../api/qualityProducts'
import { can } from '../../api/request'
import CatalogCard from '../business/CatalogCard.vue'
import CatalogPhoto from '../business/CatalogPhoto.vue'
const props = defineProps<{ product: QualityProductGroup }>()
defineEmits<{ open: [entry: QualityProductEntry, logs?: boolean]; details: []; image: [] }>()
const chosen = shallowRef('')
/** 当前展示的检测方案：未手动切换时取第一条（默认方案由目录接口顺序决定）。 */
const entry = computed(() => props.product.entries.find(item => item.workflowId === chosen.value) || props.product.entries[0])
const status = computed(() => entry.value?.status === 'disabled' ? '已停用' : entry.value?.publishedRevision ? '已发布' : '未发布')
</script>
<template>
  <CatalogCard :title="product.name" :status="status" :tone="status === '已发布' ? 'green' : 'gray'" :data-product-id="product.id">
    <dl v-if="entry">
      <div><dt>所属分类:</dt><dd class="catalog-link">{{ qualityCategories[entry.category] }}</dd></div>
      <div><dt>产品编码:</dt><dd :title="product.code">{{ product.code || '未填写' }}</dd></div>
      <div><dt>检测方案:</dt><dd class="quality-plan"><select v-if="product.entries.length > 1" :value="entry.workflowId" :aria-label="product.name + '检测方案'" @change="chosen = ($event.target as HTMLSelectElement).value"><option v-for="item in product.entries" :key="item.workflowId" :value="item.workflowId">{{ qualityCategories[item.category] }} · {{ item.templateName }}</option></select><span v-else :title="entry.templateName">{{ entry.templateName }}</span></dd></div>
      <div><dt>检测要求:</dt><dd>{{ entry.requirementCount }} 项 · {{ entry.formCount }} 张历史单据</dd></div>
    </dl>
    <template #image><CatalogPhoto :target="{ kind: 'product', id: product.id, name: product.name, image: product.image }" :editable="can('quality.edit') && can('files.create') && can('files.view')" @edit="$emit('image')" /></template>
    <template #actions>
      <button type="button" @click="$emit('details')"><i class="fa-regular fa-file-lines" aria-hidden="true" />详情</button>
      <button type="button" class="catalog-secondary" :disabled="!entry" @click="entry && $emit('open', entry, true)"><i class="fa-regular fa-clock" aria-hidden="true" />运行记录</button>
      <button type="button" class="catalog-workflow" :disabled="!entry" @click="entry && $emit('open', entry)"><i class="fa-solid fa-diagram-project" aria-hidden="true" />打开工作流</button>
    </template>
  </CatalogCard>
</template>
<style scoped>
.quality-plan { min-width: 0; }.quality-plan select { width: 100%; height: 23px; min-height: 0; padding: 0 4px; border: 1px solid #dfe6ee; border-radius: 3px; color: #505861; background: white; font-size: 12px; }
</style>
