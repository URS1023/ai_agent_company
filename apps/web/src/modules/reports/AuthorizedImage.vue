<script setup lang="ts">
/** 文件标识长期保存；临时下载地址仅留在本次组件实例，避免把签名写进报表。 */
import { inject, ref, watch } from "vue";
import { api } from "../../api/client";
import { safeAssetUrl } from "../../utils/safeContent";
import { useSession } from "../../stores/session";
const props = defineProps<{ fileId: string; alt?: string; fit?: string }>();
const session = useSession();
const resolveReportImage = inject<((fileId: string) => Promise<string>) | null>(
  "resolveReportImage",
  null,
);
const url = ref("");
const error = ref("");
const loading = ref(false);
let sequence = 0;
async function load() {
  const requestId = ++sequence;
  url.value = "";
  error.value = "";
  loading.value = true;
  try {
    const image = resolveReportImage
      ? await resolveReportImage(props.fileId)
      : "";
    if (requestId !== sequence) return;
    if (image) {
      url.value = image;
      return;
    }
    if (!session.can("m17.read")) throw new Error("无法读取图片");
    const link = await api.post<{ url: string }>(
      `/files/${encodeURIComponent(props.fileId)}/download-links`,
      {},
    );
    if (requestId === sequence) url.value = safeAssetUrl(link.url);
  } catch (e) {
    if (requestId === sequence)
      error.value = e instanceof Error ? e.message : "图片加载失败";
  } finally {
    if (requestId === sequence) loading.value = false;
  }
}
watch(() => props.fileId, load, { immediate: true });
</script>
<template>
  <img
    v-if="url"
    :src="url"
    :alt="alt ?? ''"
    :style="{ objectFit: (fit ?? 'contain') as any }"
    @error="
      error = '图片加载失败';
      url = '';
    "
  />
  <div v-else class="image-state" v-loading="loading">
    <template v-if="error"
      ><span>{{ error }}</span
      ><button @click.stop="load">重新加载图片</button></template
    >
  </div>
</template>
<style scoped>
img {
  width: 100%;
  height: 100%;
}
.image-state {
  height: 100%;
  width: 100%;
  display: grid;
  place-content: center;
  gap: 8px;
  color: #8caac0;
  font-size: 12px;
  text-align: center;
}
.image-state button {
  background: transparent;
  border: 1px solid #477c9e;
  color: #a8dcff;
  padding: 5px 9px;
  cursor: pointer;
}
</style>
