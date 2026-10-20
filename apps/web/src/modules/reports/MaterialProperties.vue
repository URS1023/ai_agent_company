<script setup lang="ts">
/** 编辑静态素材配置；只保存资源路径与展示属性，不把素材冒充业务查询结果。 */
import { computed, onMounted, ref, watch } from "vue";
import { api, request, type ListResult } from "../../api/client";
import { useSession } from "../../stores/session";
import { useOperation } from "../../composables/useOperation";
import { scopedAssetUrl } from "../../utils/safeContent";
const widget = defineModel<Record<string, any>>({ required: true });
const op = useOperation();
const catalogOp = useOperation();
const session = useSession();
const uploadInput = ref<HTMLInputElement | null>(null);
const imageError = ref("");
const assets = ref<Record<string, any>[]>([]);
const component = computed(() => widget.value.component);
watch(
  () => widget.value.id,
  () => {
    widget.value.config ??= {};
    widget.value.config.option ??= {};
  },
  { immediate: true },
);
const option = computed(() => widget.value.config.option);
const images = computed({
  get: () => widget.value.config?.images ?? [],
  set: (value: string[]) => {
    widget.value.config.images = value;
  },
});
const iconOptions = [
  { name: "星标", value: "fa-solid fa-star" },
  { name: "设备", value: "fa-solid fa-gears" },
  { name: "工厂", value: "fa-solid fa-industry" },
  { name: "品质", value: "fa-solid fa-shield-halved" },
  { name: "趋势", value: "fa-solid fa-chart-line" },
  { name: "任务", value: "fa-solid fa-clipboard-check" },
];
function selectImage() {
  delete widget.value.config.file_id;
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  imageError.value = "";
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type))
    imageError.value = "请选择 PNG、JPEG 或 WebP 图片";
  else if (file.size > 2 * 1024 * 1024) imageError.value = "图片不能超过2MB";
  if (imageError.value) {
    input.value = "";
    return;
  }
  await op.run(async () => {
    const data = new FormData();
    data.append("file", file);
    const saved = await request<{ id: string }>("/files", {
      method: "POST",
      body: data,
    });
    widget.value.config.file_id = saved.id;
    widget.value.config.imageUrl = "";
  });
  input.value = "";
}
onMounted(() => {
  if (["JImg", "JCarousel"].includes(component.value)) {
    catalogOp.run(async () => {
      const response = await api.get<ListResult>("/report-components/catalog");
      assets.value = response.items.filter((item) => item.imageUrl);
    });
  }
});
</script>
<template>
  <template v-if="['JDragBorder', 'JDragDecoration'].includes(component)">
    <el-form-item label="主色"
      ><el-color-picker v-model="option.mainColor" show-alpha
    /></el-form-item>
    <el-form-item label="辅助色"
      ><el-color-picker v-model="option.subColor" show-alpha
    /></el-form-item>
    <el-form-item
      v-if="component === 'JDragBorder' && String(option.type) === '5'"
      label="边框标题"
      ><el-input v-model="option.title"
    /></el-form-item>
  </template>
  <template v-else-if="['JImg', 'JCarousel'].includes(component)">
    <el-form-item :label="component === 'JCarousel' ? '轮播图片' : '图片'">
      <el-select
        v-if="component === 'JCarousel'"
        v-model="images"
        multiple
        filterable
        collapse-tags
        :loading="catalogOp.busy.value"
      >
        <el-option
          v-for="asset in assets"
          :key="asset.id"
          :value="asset.imageUrl"
          :label="asset.name"
          ><img
            class="asset-option"
            :src="scopedAssetUrl(asset.imageUrl, '/')"
            alt=""
          /><span>{{ asset.name }}</span></el-option
        >
      </el-select>
      <el-select
        v-else
        v-model="widget.config.imageUrl"
        clearable
        filterable
        :loading="catalogOp.busy.value"
        @change="selectImage"
      >
        <el-option
          v-for="asset in assets"
          :key="asset.id"
          :value="asset.imageUrl"
          :label="asset.name"
          ><img
            class="asset-option"
            :src="scopedAssetUrl(asset.imageUrl, '/')"
            alt=""
          /><span>{{ asset.name }}</span></el-option
        >
      </el-select>
    </el-form-item>
    <el-form-item
      v-if="component === 'JImg' && session.can('m17.write')"
      :error="imageError"
      ><input
        ref="uploadInput"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        hidden
        @change="upload"
      /><el-button :loading="op.busy.value" @click="uploadInput?.click()"
        >上传图片</el-button
      ><el-button
        v-if="widget.config.file_id"
        link
        type="danger"
        @click="delete widget.config.file_id"
        >移除</el-button
      ></el-form-item
    >
    <el-form-item v-if="component === 'JImg'" label="适应方式"
      ><el-select v-model="option.fit"
        ><el-option label="完整显示" value="contain" /><el-option
          label="填满裁剪"
          value="cover" /><el-option label="拉伸" value="fill" /></el-select
    ></el-form-item>
    <el-form-item v-else label="切换间隔"
      ><el-input-number
        v-model="option.interval"
        :min="2"
        :max="60"
        :step="1"
      /><span class="seconds-unit">秒</span></el-form-item
    >
  </template>
  <template v-else-if="component === 'JCustomIcon'">
    <el-form-item label="图标"
      ><el-select v-model="option.icon"
        ><el-option
          v-for="icon in iconOptions"
          :key="icon.value"
          :value="icon.value"
          :label="icon.name"
          ><i :class="icon.value" /> {{ icon.name }}</el-option
        ></el-select
      ></el-form-item
    >
    <el-form-item label="图标颜色"
      ><el-color-picker v-model="option.iconColor" show-alpha
    /></el-form-item>
  </template>
</template>
<style scoped>
.asset-option {
  display: inline-block;
  width: 28px;
  height: 26px;
  object-fit: contain;
  vertical-align: middle;
  margin-right: 10px;
}
.seconds-unit {
  margin-left: 8px;
  color: var(--text-muted);
}
</style>
