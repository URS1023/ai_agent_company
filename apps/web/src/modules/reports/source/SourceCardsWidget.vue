<script setup lang="ts">
/** 来源Test5/apps/analytics/src/components/jimu-screen/JimuBusinessWidget.vue的卡片布局；翻页/播放只改变可见页。 */
import { computed, onBeforeUnmount, onMounted, shallowRef, watch } from "vue";
import {
  cardVariant,
  pageCount,
  pageIndex,
  pageRows,
  type SourcePresentationModel,
  type SourcePresentationRow,
} from "../sourcePresentation";
const props = defineProps<{
  widget: Record<string, any>;
  model: SourcePresentationModel;
  preview?: boolean;
}>();
const emit = defineEmits<{ "data-click": [row: SourcePresentationRow] }>();
const page = shallowRef(0);
const paused = shallowRef(false);
const hovered = shallowRef(false);
const focused = shallowRef(false);
const mounted = shallowRef(false);
const visible = shallowRef(true);
const reducedMotion = shallowRef(false);
const variant = computed(() => cardVariant(props.widget, props.model));
const settings = computed(() => props.widget.config?.option?.carousel ?? {});
const pageSize = computed(() =>
  Math.min(
    8,
    Math.max(
      1,
      Math.floor(
        Number(settings.value.page_size) ||
          (variant.value.mode === "carousel" ? 1 : 3),
      ),
    ),
  ),
);
const count = computed(() =>
  pageCount(props.model.rows.length, pageSize.value),
);
const cards = computed(() =>
  pageRows(props.model.rows, page.value, pageSize.value),
);
const playing = computed(
  () =>
    !!props.preview &&
    settings.value.autoplay === true &&
    count.value > 1 &&
    !paused.value &&
    !hovered.value &&
    !focused.value &&
    visible.value &&
    !reducedMotion.value,
);
let timer: ReturnType<typeof setInterval> | undefined;
let motion: MediaQueryList | undefined;
function stopTimer() {
  if (timer) clearInterval(timer);
  timer = undefined;
}
function change(offset: number) {
  page.value = pageIndex(page.value, offset, count.value);
}
watch(
  () => [props.widget.id, props.model.rows, pageSize.value] as const,
  () => {
    page.value = 0;
    paused.value = false;
  },
);
watch(
  () =>
    [playing.value, mounted.value, settings.value.interval_seconds] as const,
  () => {
    stopTimer();
    if (playing.value && mounted.value)
      timer = setInterval(
        () => change(1),
        Math.min(
          60,
          Math.max(3, Number(settings.value.interval_seconds) || 5),
        ) * 1000,
      );
  },
);
function visibilityChanged() {
  visible.value = document.visibilityState !== "hidden";
}
function motionChanged() {
  reducedMotion.value = motion?.matches ?? false;
}
onMounted(() => {
  mounted.value = true;
  if (typeof document !== "undefined") {
    visibilityChanged();
    document.addEventListener("visibilitychange", visibilityChanged);
  }
  if (typeof window !== "undefined" && window.matchMedia) {
    motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    motionChanged();
    motion.addEventListener("change", motionChanged);
  }
});
onBeforeUnmount(() => {
  stopTimer();
  if (typeof document !== "undefined")
    document.removeEventListener("visibilitychange", visibilityChanged);
  motion?.removeEventListener("change", motionChanged);
});
</script>

<template>
  <section
    class="card-sequence"
    :class="{
      vertical: variant.direction === 'vertical',
      highlight: variant.highlight,
      carousel: variant.mode === 'carousel',
    }"
    @mouseenter="hovered = true"
    @mouseleave="hovered = false"
    @focusin="focused = true"
    @focusout="focused = false"
  >
    <div class="card-track">
      <button
        v-for="(row, index) in cards"
        :key="`${page}-${index}`"
        class="sequence-card"
        :class="{ active: variant.highlight && index === 0 }"
        @pointerdown.stop
        @click.stop="emit('data-click', row)"
      >
        <b v-if="variant.show_index" class="card-index">{{
          String(page * pageSize + index + 1).padStart(2, "0")
        }}</b>
        <span class="card-content"
          ><strong class="card-name">{{ row.name }}</strong
          ><span v-if="row.description" class="card-description">{{
            row.description
          }}</span
          ><time v-if="row.time" class="card-time" :datetime="row.time">{{
            row.time_display ?? row.time
          }}</time></span
        >
        <strong v-if="row.value !== null" class="card-value"
          >{{ row.value }}<span class="card-unit">{{ row.unit }}</span></strong
        >
      </button>
    </div>
    <nav
      v-if="count > 1"
      class="card-controls"
      aria-label="卡片分页"
      @pointerdown.stop
    >
      <button
        class="card-control"
        aria-label="上一页卡片"
        @click.stop="change(-1)"
      >
        ‹
      </button>
      <span class="card-page" aria-live="polite"
        >{{ page + 1 }} / {{ count }}</span
      >
      <button
        class="card-control"
        aria-label="下一页卡片"
        @click.stop="change(1)"
      >
        ›
      </button>
      <button
        v-if="preview && settings.autoplay && !reducedMotion"
        class="card-control"
        :aria-pressed="paused"
        @click.stop="paused = !paused"
      >
        {{ paused ? "播放" : "暂停" }}
      </button>
    </nav>
  </section>
</template>

<style scoped>
.card-sequence {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 10px;
  box-sizing: border-box;
  background: #071b2d;
}
.card-track {
  display: flex;
  flex: 1;
  min-height: 0;
  gap: 10px;
  overflow: auto;
}
.sequence-card {
  display: flex;
  flex-direction: column;
  flex: 1 0 110px;
  min-width: 0;
  gap: 10px;
  padding: 16px 12px;
  border: 1px solid #244762;
  background: linear-gradient(145deg, #0c2e48, #071b2d);
  color: #dcecff;
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.card-content {
  display: grid;
  flex: 1;
  min-width: 0;
  align-content: start;
  gap: 8px;
}
.card-name {
  font-size: 16px;
  font-weight: 600;
  color: #eefaff;
  overflow-wrap: anywhere;
}
.card-description {
  color: #9ab7ca;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.card-time {
  color: #8fa6b9;
  overflow-wrap: anywhere;
}
.card-value {
  color: #51c8ef;
  font-size: 22px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.card-unit {
  color: #97b7cd;
  margin-left: 3px;
  font-size: 13px;
}
.card-controls {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 8px;
}
.card-control {
  padding: 2px 8px;
  border: 1px solid #315c76;
  color: #d3edfa;
  background: #0b304a;
  font: inherit;
  cursor: pointer;
}
.card-page {
  color: #9ab7ca;
}
.vertical .card-track {
  flex-direction: column;
}
.vertical .sequence-card {
  flex-direction: row;
  align-items: center;
  flex-basis: auto;
  padding: 10px 12px;
}
.card-index {
  font-size: 22px;
  color: #4fb4dd;
  padding-right: 10px;
}
.vertical .card-content {
  flex: 1;
}
.highlight .sequence-card {
  opacity: 0.7;
  border-color: #27485f;
}
.highlight .sequence-card.active {
  flex-grow: 1.5;
  opacity: 1;
  border-color: #32b8e8;
  box-shadow: inset 2px 0 #32b8e8;
}
.carousel .sequence-card {
  justify-content: center;
}
.sequence-card:focus-visible,
.card-control:focus-visible {
  outline: 2px solid #42bdf0;
  outline-offset: 1px;
}
</style>
