<script setup lang="ts">
/** 来源Test5/apps/analytics/src/components/jimu-screen/JimuBusinessWidget.vue的月历布局；月份选择只浏览已授权结果。 */
import { computed, shallowRef, watch } from "vue";
import {
  calendarGrid,
  firstCalendarDate,
  shanghaiDate,
  shiftMonth,
  validMonth,
  type SourcePresentationModel,
  type SourcePresentationRow,
} from "../sourcePresentation";
const props = defineProps<{
  widget: Record<string, any>;
  model: SourcePresentationModel;
}>();
const emit = defineEmits<{ "data-click": [row: SourcePresentationRow] }>();
const month = shallowRef("");
const selectedDate = shallowRef("");
const view = shallowRef<"month" | "agenda">("month");
const today = shanghaiDate();
const multiView = computed(
  () => props.widget.component === "JMultiViewCalendar",
);
const monthRows = computed(() =>
  props.model.rows.filter((row) => row.date?.startsWith(month.value)),
);
const selectedRows = computed(() =>
  props.model.rows.filter((row) => row.date === selectedDate.value),
);
const agendaRows = computed(() =>
  view.value === "agenda" ? monthRows.value : selectedRows.value,
);
const cells = computed(() =>
  calendarGrid(month.value, props.model.rows, today),
);
const monthLabel = computed(
  () => `${Number(month.value.slice(0, 4))}年${Number(month.value.slice(5))}月`,
);
watch(
  () => [props.widget.id, props.model.calendar?.month] as const,
  () => {
    const configured = props.model.calendar?.month;
    month.value = validMonth(configured)
      ? configured
      : (props.model.rows.find((row) => row.date)?.date?.slice(0, 7) ??
        today.slice(0, 7));
    selectedDate.value = firstCalendarDate(
      month.value,
      props.model.rows,
      today,
    );
    view.value = "month";
  },
  { immediate: true },
);
function changeMonth(offset: number) {
  month.value = shiftMonth(month.value, offset);
  selectedDate.value = firstCalendarDate(month.value, props.model.rows, today);
}
function goToday() {
  month.value = today.slice(0, 7);
  selectedDate.value = today;
  view.value = "month";
}
</script>

<template>
  <section
    class="calendar-widget"
    :class="{
      'permanent-calendar': widget.component === 'JPermanentCalendar',
      'multi-calendar': multiView,
    }"
  >
    <header class="calendar-toolbar" @pointerdown.stop>
      <strong class="calendar-month">{{ monthLabel }}</strong>
      <nav class="calendar-navigation" aria-label="月份">
        <button
          class="calendar-control"
          aria-label="上个月"
          :disabled="shiftMonth(month, -1) === month"
          @click.stop="changeMonth(-1)"
        >
          ‹</button
        ><button class="calendar-control" @click.stop="goToday">今天</button
        ><button
          class="calendar-control"
          aria-label="下个月"
          :disabled="shiftMonth(month, 1) === month"
          @click.stop="changeMonth(1)"
        >
          ›
        </button>
      </nav>
      <nav v-if="multiView" class="calendar-views" aria-label="日历视图">
        <button
          class="calendar-control"
          :aria-pressed="view === 'month'"
          @click.stop="view = 'month'"
        >
          月</button
        ><button
          class="calendar-control"
          :aria-pressed="view === 'agenda'"
          @click.stop="view = 'agenda'"
        >
          日程
        </button>
      </nav>
    </header>
    <div
      class="calendar-body"
      :class="{ 'with-agenda': multiView && view === 'month' }"
    >
      <div v-if="view === 'month'" class="calendar-month-grid">
        <div class="calendar-week">
          <span
            v-for="day in ['一', '二', '三', '四', '五', '六', '日']"
            :key="day"
            >{{ day }}</span
          >
        </div>
        <div class="calendar-days">
          <button
            v-for="cell in cells"
            :key="cell.date"
            class="calendar-day"
            :class="{
              muted: !cell.inMonth,
              today: cell.today,
              selected: cell.date === selectedDate,
            }"
            :aria-label="`${cell.date}，${cell.rows.length}项日程`"
            :aria-pressed="cell.date === selectedDate"
            @pointerdown.stop
            @click.stop="selectedDate = cell.date"
          >
            <span>{{ cell.day }}</span
            ><b v-if="cell.rows.length" class="calendar-event-count">{{
              cell.rows.length
            }}</b>
          </button>
        </div>
      </div>
      <aside
        class="calendar-agenda"
        :class="{ 'agenda-only': view === 'agenda' }"
      >
        <time class="agenda-date">{{
          view === "agenda" ? monthLabel : selectedDate
        }}</time>
        <button
          v-for="(row, index) in agendaRows"
          :key="`${row.time}-${index}`"
          class="agenda-item"
          @pointerdown.stop
          @click.stop="emit('data-click', row)"
        >
          <span class="agenda-name">{{ row.name }}</span
          ><time v-if="view === 'agenda'" class="agenda-time">{{
            row.date
          }}</time
          ><span v-if="row.description" class="agenda-description">{{
            row.description
          }}</span>
        </button>
        <div v-if="!agendaRows.length" class="calendar-empty">暂无日程</div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.calendar-widget {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 10px;
  box-sizing: border-box;
  background: linear-gradient(150deg, #0c2a47, #051527);
  border: 1px solid #295775;
}
.calendar-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-height: 30px;
  margin-bottom: 8px;
}
.calendar-month {
  flex: 1;
  white-space: nowrap;
  font-size: 15px;
}
.calendar-navigation,
.calendar-views {
  display: flex;
  gap: 3px;
}
.calendar-control {
  border: 1px solid #31556f;
  padding: 3px 6px;
  color: #cbe4f5;
  background: #0a2942;
  font: inherit;
  cursor: pointer;
}
.calendar-control[aria-pressed="true"] {
  border-color: #42b7ea;
  color: var(--surface);
  background: #17618c;
}
.calendar-control:disabled {
  cursor: default;
  opacity: 0.4;
}
.calendar-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  gap: 8px;
}
.calendar-body.with-agenda {
  flex-direction: row;
}
.calendar-month-grid {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 160px;
}
.calendar-week,
.calendar-days {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 2px;
}
.calendar-week {
  padding-bottom: 5px;
  color: #93b0c7;
  text-align: center;
  font-size: 12px;
}
.calendar-days {
  flex: 1;
  grid-template-rows: repeat(6, minmax(22px, 1fr));
}
.calendar-day {
  position: relative;
  min-width: 0;
  padding: 3px;
  border: 1px solid transparent;
  background: transparent;
  color: #d9edfa;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.calendar-day.muted {
  color: #526e84;
}
.calendar-day.today {
  border-color: #2b8bb5;
}
.calendar-day.selected {
  background: #125b88;
  border-color: #49c5ef;
}
.calendar-event-count {
  float: right;
  color: #44d6bb;
  font-size: 11px;
}
.calendar-agenda {
  min-height: 42px;
  max-height: 100px;
  overflow: auto;
  border-top: 1px solid #28506d;
  padding-top: 6px;
}
.with-agenda .calendar-agenda {
  flex: 0 0 34%;
  max-height: none;
  border: 0;
  border-left: 1px solid #28506d;
  padding: 0 0 0 8px;
}
.calendar-agenda.agenda-only {
  flex: 1;
  max-height: none;
}
.agenda-date {
  display: block;
  color: #96b5c9;
  margin-bottom: 6px;
}
.agenda-item {
  display: grid;
  gap: 4px;
  width: 100%;
  padding: 7px 0;
  border: 0;
  border-bottom: 1px solid #21465f;
  color: inherit;
  background: transparent;
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.agenda-name {
  color: #ecf9ff;
  overflow-wrap: anywhere;
}
.agenda-time,
.agenda-description {
  color: #94adc0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.calendar-empty {
  color: #8faabd;
  padding: 8px 0;
}
.permanent-calendar {
  border: 0;
  background: #071b2d;
}
.calendar-control:focus-visible,
.calendar-day:focus-visible,
.agenda-item:focus-visible {
  outline: 2px solid #48c2ed;
  outline-offset: 1px;
}
</style>
