<script setup lang="ts">
/** 精提Test5 JimuWidgetRenderer文字/表格、JimuBusinessWidget排名/列表/卡片；事实仅来自服务端模型。 */
import { computed, onBeforeUnmount, shallowRef, watch } from "vue";
import ScrollingBoard from "./ScrollingBoard.vue";
import {
  tableTypes,
  rankTypes,
  listTypes,
  cardTypes,
  numberTypes,
  progressTypes,
  safeRichText,
  type PresentationModel,
} from "../presentation";
const props = defineProps<{ widget: Record<string, any>; preview?: boolean }>();
const emit = defineEmits<{
  "data-click": [point: { data: unknown; dataIndex: number }];
}>();
const component = computed(() => props.widget.component as string);
const option = computed(() => props.widget.config?.option ?? {});
const model = computed(
  () => props.widget.presentation_model as PresentationModel | undefined,
);
const rows = computed(() => model.value?.rows ?? []);
const now = shallowRef(new Date());
let timer: ReturnType<typeof setInterval> | undefined;
watch(
  component,
  (value) => {
    if (timer) clearInterval(timer);
    timer =
      value === "JCurrentTime"
        ? setInterval(() => {
            now.value = new Date();
          }, 1000)
        : undefined;
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});
const clock = computed(() =>
  new Intl.DateTimeFormat("sv-SE", {
    timeZone: option.value.timezone ?? "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(option.value.date_only
      ? {}
      : {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        }),
  }).format(now.value),
);
const bodyStyle = computed(() => ({
  color: option.value.body?.color ?? "#d0e0f0",
  fontSize: `${Math.max(8, Math.min(160, Number(option.value.body?.fontSize) || (numberTypes.includes(component.value) ? 40 : component.value === "JCurrentTime" ? 24 : 16)))}px`,
  fontWeight: option.value.body?.fontWeight ?? "normal",
  textAlign: option.value.body?.textAlign ?? "center",
}));
const html = computed(() => safeRichText(option.value.body?.text));
const fields = computed(
  () => model.value?.columns.map((column) => column.field) ?? [],
);
const headers = computed(
  () => model.value?.columns.map((column) => column.label) ?? [],
);
const error = computed(
  () =>
    model.value?.message ||
    (model.value?.status === "empty"
      ? "暂无数据"
      : props.widget.binding
        ? "刷新数据"
        : "选择数据查询"),
);
const animated = computed(
  () =>
    props.preview &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
);
const displayed = computed(() =>
  component.value === "JFlashList" ? rows.value.slice(0, 4) : rows.value,
);
const palette = ["#38bdf8", "#22d3a6", "#8b7cf6", "#f0b65b"];
const ring = computed(() =>
  ["JRingProgress", "JRoundProgress"].includes(component.value),
);
</script>

<template>
  <div class="presentation-widget" :data-presentation-component="component">
    <div
      v-if="component === 'JCurrentTime'"
      class="presentation-number"
      :style="bodyStyle"
    >
      {{ clock }}
    </div>
    <div
      v-else-if="component === 'JDragEditor'"
      class="presentation-rich"
      :style="bodyStyle"
      v-html="html"
    />
    <div
      v-else-if="model?.status !== 'ready'"
      class="presentation-empty"
      role="status"
    >
      {{ error }}
    </div>
    <ScrollingBoard
      v-else-if="tableTypes.includes(component)"
      :headers="headers"
      :fields="fields"
      :rows="rows"
      :ranking="component === 'JScrollRankingBoard'"
      :animated="animated && component !== 'JCommonTable'"
      :style="{
        '--board-grid': `${component === 'JScrollRankingBoard' ? '34px ' : ''}repeat(${fields.length}, minmax(0,1fr))`,
      }"
    />
    <div
      v-else-if="numberTypes.includes(component)"
      class="presentation-number"
      :class="{ 'count-tiles': component === 'JCountTo' }"
      :style="bodyStyle"
    >
      {{ rows[0]?.value ?? "—" }}{{ rows[0]?.unit }}
    </div>
    <div v-else-if="ring" class="progress-ring">
      <svg viewBox="0 0 240 240">
        <circle cx="120" cy="120" r="85" class="ring-track" />
        <circle
          v-if="rows[0]?.percentage != null"
          cx="120"
          cy="120"
          r="85"
          class="ring-value"
          :style="{
            strokeDasharray: `${Number(rows[0]?.percentage) * Math.PI * 1.7} ${Math.PI * 170}`,
          }"
        />
        <text x="120" y="125" text-anchor="middle">
          {{ rows[0]?.value ?? "—" }}/{{ rows[0]?.total }}
        </text>
      </svg>
    </div>
    <div
      v-else-if="progressTypes.includes(component)"
      class="presentation-rows"
    >
      <div v-for="(row, index) in rows" :key="index" class="progress-item">
        <span>{{ row.name }}</span
        ><b>{{ row.value ?? "—" }}/{{ row.total }}</b>
        <div class="progress-track">
          <i
            v-if="row.percentage != null"
            :style="{
              width: `${Number(row.percentage)}%`,
              background: palette[index % palette.length],
            }"
          />
        </div>
      </div>
    </div>
    <div
      v-else-if="cardTypes.includes(component)"
      class="metric-cards"
      :class="{
        'color-cards': component === 'JColorBlock',
        'simple-cards': component === 'JSimpleCard',
      }"
    >
      <article
        v-for="(row, index) in rows"
        :key="index"
        :style="{ '--card-color': palette[index % palette.length] }"
        @click="emit('data-click', { data: row, dataIndex: index })"
      >
        <span>{{ row.name }}</span
        ><strong
          >{{ row.value ?? "—" }}<small>{{ row.unit }}</small></strong
        >
      </article>
    </div>
    <ScrollingBoard
      v-else-if="component === 'JScrollList'"
      :headers="[]"
      :fields="[
        'name',
        ...(rows.some((row) => row.description) ? ['description'] : []),
        ...(rows.some((row) => row.time) ? ['time'] : []),
      ]"
      :rows="rows"
      :animated="animated"
      :style="{
        '--board-grid': `repeat(${1 + Number(rows.some((row) => row.description)) + Number(rows.some((row) => row.time))},minmax(0,1fr))`,
      }"
    />
    <div
      v-else-if="rankTypes.includes(component) || listTypes.includes(component)"
      class="presentation-rows"
      :class="{ 'flash-ranking': component === 'JFlashList' }"
    >
      <div
        v-for="(row, index) in displayed"
        :key="index"
        class="list-row"
        @click="emit('data-click', { data: row, dataIndex: index })"
      >
        <b
          v-if="rankTypes.includes(component)"
          class="rank"
          :style="{ color: palette[index % palette.length] }"
          >{{ index + 1 }}</b
        ><span
          >{{ row.name
          }}<small v-if="row.description">{{ row.description }}</small></span
        ><strong v-if="rankTypes.includes(component)"
          >{{ row.value ?? "—" }}{{ row.unit }}</strong
        ><time v-else>{{ row.time }}</time>
      </div>
    </div>
  </div>
</template>

<style scoped>
.presentation-widget {
  height: 100%;
  min-height: 0;
  color: #d0e0f0;
  font-size: 14px;
  overflow: auto;
}
.presentation-number {
  height: 100%;
  display: grid;
  place-items: center;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.count-tiles {
  font-variant-numeric: tabular-nums;
  letter-spacing: 3px;
}
.presentation-rich {
  padding: 12px;
  overflow-wrap: anywhere;
}
.presentation-empty {
  height: 100%;
  display: grid;
  place-items: center;
  color: #8b9fb8;
}
.presentation-rows {
  padding: 10px;
}
.list-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 8px;
  border-bottom: 1px solid #6586b030;
}
.list-row > span {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.list-row small {
  display: block;
  opacity: 0.7;
  margin-top: 5px;
}
.list-row strong,
.list-row time {
  max-width: 45%;
  overflow-wrap: anywhere;
  font-weight: normal;
}
.rank {
  font-size: 18px;
}
.flash-ranking .list-row {
  margin: 7px 0;
  border: 1px solid #6386b035;
  background: linear-gradient(90deg, #3265a433, transparent);
}
.metric-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px;
  padding: 12px;
}
.metric-cards article {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  background: #1e365a44;
  border-left: 3px solid var(--card-color);
}
.metric-cards strong {
  color: var(--card-color);
  font-size: 28px;
  overflow-wrap: anywhere;
}
.metric-cards small {
  font-size: 14px;
  margin-left: 5px;
}
.color-cards article {
  background: color-mix(in srgb, var(--card-color) 25%, transparent);
}
.simple-cards article {
  background: transparent;
  border: 0;
}
.progress-item {
  padding: 10px;
}
.progress-item b {
  float: right;
  font-weight: normal;
}
.progress-track {
  height: 10px;
  background: #24354b;
  margin-top: 10px;
  clear: both;
}
.progress-track i {
  display: block;
  height: 100%;
}
.progress-ring {
  height: 100%;
  display: grid;
  place-items: center;
}
.progress-ring svg {
  height: 100%;
  max-width: 100%;
}
.ring-track,
.ring-value {
  fill: none;
  stroke-width: 15;
}
.ring-track {
  stroke: #24354b;
}
.ring-value {
  stroke: #38bdf8;
  transform: rotate(-90deg);
  transform-origin: center;
}
.progress-ring text {
  fill: currentColor;
  font-size: 18px;
}
</style>
