<script setup>
/**
 * JimuBusinessWidget —— 大屏业务类组件的渲染器（日历、指标卡、榜单、列表等非图表组件）。
 *
 * 使用场景：JimuWidgetRenderer 判定为业务组件（isBusinessWidgetType）时渲染。
 * 依赖接口：`utils/safeContent.scopedAssetUrl`（带作用域的静态资源地址）。
 * 关键约束：
 * - 组件类型由 component 名精确匹配（isCalendar/isMetricCards/isRankList/isRecordList），
 *   新增业务组件必须同步 businessWidgetTypes.js 与本文件的分支，否则会落到默认空态；
 * - 数据兼容三种形态：数组、{ items: [] } 包装、单个对象；无法识别时返回空数组而不是抛错；
 * - interactive 为 false（预览/查看态）时不派发 action，交互仅在设计态可用。
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { scopedAssetUrl } from "@/utils/safeContent";
import AuthorizedImage from "../AuthorizedImage.vue";

const props = defineProps({
  widget: { type: Object, required: true },
  data: { type: [Array, Object], default: () => [] },
  interactive: { type: Boolean, default: false },
});
const emit = defineEmits(["action"]);

const activeTab = ref(0);
const componentType = computed(() => String(props.widget.component || ""));
const option = computed(() => props.widget.config?.option || {});
/** 数据归一化：兼容数组、{ items } 包装与单对象三种形态；空对象返回空数组避免渲染一行空数据。 */
const rows = computed(() => {
  if (Array.isArray(props.data)) return props.data;
  if (Array.isArray(props.data?.items)) return props.data.items;
  if (
    props.data &&
    typeof props.data === "object" &&
    Object.keys(props.data).length
  )
    return [props.data];
  return [];
});
const isCalendar = computed(() =>
  ["JCalendar", "JMultiViewCalendar", "JPermanentCalendar"].includes(
    componentType.value,
  ),
);
const isMetricCards = computed(() =>
  ["JGrowCard", "JSimpleCard", "JColorBlock"].includes(componentType.value),
);
const isRankList = computed(() =>
  ["JFlashList", "JRankingList"].includes(componentType.value),
);
const isRecordList = computed(() =>
  ["JList", "JScrollList"].includes(componentType.value),
);
const isCarousel = computed(() =>
  ["JCardCarousel", "JCardScroll", "JCarousel"].includes(componentType.value),
);
const isForm = computed(() =>
  ["JForm", "online", "design"].includes(componentType.value),
);
const imageUrl = computed(() =>
  assetUrl(props.widget.config?.imageUrl || props.widget.config?.src || ""),
);
const carouselIndex = ref(0);
let carouselTimer = null;
function stopCarousel() {
  if (carouselTimer) clearInterval(carouselTimer);
  carouselTimer = null;
}
function moveCarousel(offset) {
  if (rows.value.length)
    carouselIndex.value =
      (carouselIndex.value + offset + rows.value.length) % rows.value.length;
}
// 尊重减少动效设置；离开报表时释放计时器，不发起资源外的网络请求。
watch(
  () => [componentType.value, rows.value.length, option.value.interval],
  () => {
    stopCarousel();
    carouselIndex.value = 0;
    if (
      componentType.value === "JCarousel" &&
      rows.value.length > 1 &&
      !window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      const seconds = Math.max(
        2,
        Math.min(60, Number(option.value.interval) || 5),
      );
      carouselTimer = setInterval(() => moveCarousel(1), seconds * 1000);
    }
  },
  { immediate: true },
);
onBeforeUnmount(stopCarousel);
const currentDate = new Date();
const selectedMonth = computed(() => {
  const configured = option.value.calendar?.month || option.value.month;
  const date = configured ? new Date(`${configured}-01T00:00:00`) : currentDate;
  return Number.isNaN(date.getTime()) ? currentDate : date;
});
const monthLabel = computed(
  () =>
    `${selectedMonth.value.getFullYear()} 年 ${selectedMonth.value.getMonth() + 1} 月`,
);
const weekdays = ["一", "二", "三", "四", "五", "六", "日"];
const calendarDays = computed(() => {
  const year = selectedMonth.value.getFullYear();
  const month = selectedMonth.value.getMonth();
  const firstOffset = (new Date(year, month, 1).getDay() + 6) % 7;
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(year, month, index - firstOffset + 1);
    const iso = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    const events = rows.value.filter(
      (item) =>
        String(pick(item, ["date", "start", "开始", "time"])).slice(0, 10) ===
        iso,
    );
    return {
      iso,
      day: date.getDate(),
      inMonth: date.getMonth() === month,
      today: date.toDateString() === currentDate.toDateString(),
      events,
    };
  });
});
const filterFields = computed(() => {
  const configured = option.value.fields;
  if (Array.isArray(configured) && configured.length) return configured;
  if (rows.value.length && rows.value[0] && typeof rows.value[0] === "object") {
    return Object.keys(rows.value[0])
      .slice(0, 3)
      .map((name, index) => ({
        name,
        label: name,
        type: index === 0 ? "text" : "select",
      }));
  }
  return [
    { name: "keyword", label: "关键词", type: "text" },
    { name: "status", label: "状态", type: "select" },
    { name: "date", label: "日期范围", type: "date" },
  ];
});
const maxRankValue = computed(() =>
  Math.max(1, ...rows.value.map((item) => numberValue(item))),
);

function pick(item, keys, fallback = "") {
  if (item == null) return fallback;
  if (typeof item !== "object") return item;
  for (const key of keys) {
    if (item[key] !== undefined && item[key] !== null && item[key] !== "")
      return item[key];
  }
  return fallback;
}

function assetUrl(value) {
  return scopedAssetUrl(value, import.meta.env.BASE_URL);
}

function itemName(item, index = 0) {
  return String(
    pick(
      item,
      ["name", "title", "label", "项目", "名称", "标题"],
      `记录 ${index + 1}`,
    ),
  );
}

function itemDescription(item) {
  return String(
    pick(
      item,
      ["description", "desc", "content", "detail", "描述", "内容"],
      "",
    ),
  );
}

function itemTime(item) {
  return String(
    pick(item, ["time", "date", "start", "updatedAt", "时间", "日期"], ""),
  );
}

function numberValue(item) {
  const value = Number(
    pick(item, ["value", "amount", "total", "count", "数值", "金额"], 0),
  );
  return Number.isFinite(value) ? value : 0;
}

function formatValue(item) {
  const raw = pick(
    item,
    ["value", "amount", "total", "count", "数值", "金额"],
    "-",
  );
  const value = Number(raw);
  return Number.isFinite(value)
    ? value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })
    : String(raw);
}

function colorAt(item, index) {
  return String(
    pick(
      item,
      ["color", "颜色"],
      option.value.colors?.[index] ||
        ["#38bdf8", "#22d3a6", "#8b7cf6", "#f0b65b"][index % 4],
    ),
  );
}

function progress(item) {
  const value = numberValue(item);
  const total =
    Number(pick(item, ["total", "target", "总计", "目标"], 100)) || 100;
  return Math.max(0, Math.min(100, (value / total) * 100));
}

function iconClass(item, index = 0) {
  const source = String(pick(item, ["icon", "图标"], ""));
  if (source.startsWith("fa-")) return source;
  return [
    "fa-solid fa-chart-line",
    "fa-solid fa-cubes-stacked",
    "fa-solid fa-shield-halved",
    "fa-solid fa-bolt",
    "fa-solid fa-diagram-project",
    "fa-solid fa-clipboard-check",
  ][index % 6];
}

function initials(item, index = 0) {
  const name = itemName(item, index);
  return name.slice(0, 1).toUpperCase();
}

function trigger(item, index = 0) {
  if (!props.interactive) return;
  emit("action", {
    name: itemName(item, index),
    value: numberValue(item),
    data: item,
    dataIndex: index,
  });
}

function ganttLeft(item, index) {
  const value = Number(
    pick(item, ["startPercent", "start", "开始"], index * 12),
  );
  return `${Math.max(0, Math.min(82, Number.isFinite(value) ? value : index * 12))}%`;
}

function ganttWidth(item) {
  const value = Number(
    pick(item, ["progress", "duration", "value", "进度"], 35),
  );
  return `${Math.max(8, Math.min(72, Number.isFinite(value) ? value : 35))}%`;
}
</script>

<template>
  <div
    class="business-widget"
    :class="`business-${componentType}`"
    :data-business-component="componentType"
  >
    <section
      v-if="isCalendar"
      class="calendar-widget"
      :class="{ 'multi-view': componentType === 'JMultiViewCalendar' }"
    >
      <header>
        <div>
          <small>CALENDAR</small><strong>{{ monthLabel }}</strong>
        </div>
        <span
          ><i class="fa-solid fa-chevron-left"></i><b>今天</b
          ><i class="fa-solid fa-chevron-right"></i
        ></span>
      </header>
      <div class="calendar-layout">
        <div class="calendar-month">
          <div class="calendar-week">
            <b v-for="day in weekdays" :key="day">{{ day }}</b>
          </div>
          <div class="calendar-grid">
            <div
              v-for="day in calendarDays"
              :key="day.iso"
              :class="{
                muted: !day.inMonth,
                today: day.today,
                busy: day.events.length,
              }"
            >
              <span>{{ day.day }}</span
              ><i v-if="day.events.length"></i>
            </div>
          </div>
        </div>
        <aside v-if="componentType === 'JMultiViewCalendar'">
          <small>本月安排</small>
          <button
            v-for="(item, index) in rows.slice(0, 5)"
            :key="index"
            @click="trigger(item, index)"
          >
            <i :style="{ background: colorAt(item, index) }"></i
            ><span
              ><b>{{ itemName(item, index) }}</b
              ><em>{{ itemTime(item) }}</em></span
            >
          </button>
          <p v-if="!rows.length">暂无日程</p>
        </aside>
      </div>
    </section>

    <section v-else-if="componentType === 'JFilterQuery'" class="filter-widget">
      <label
        v-for="field in filterFields.slice(0, 3)"
        :key="field.name || field.label"
      >
        <span>{{ field.label || field.name }}</span>
        <span class="filter-control"
          >{{
            field.placeholder ||
            (field.type === "date"
              ? "开始日期 — 结束日期"
              : field.type === "select"
                ? "全部"
                : "请输入")
          }}<i
            :class="
              field.type === 'date'
                ? 'fa-regular fa-calendar'
                : field.type === 'select'
                  ? 'fa-solid fa-chevron-down'
                  : 'fa-solid fa-magnifying-glass'
            "
          ></i
        ></span>
      </label>
      <button @click="trigger({ name: '查询' })">
        <i class="fa-solid fa-filter"></i>查询
      </button>
      <button class="secondary" @click="trigger({ name: '重置' })">重置</button>
    </section>

    <button
      v-else-if="componentType === 'JCustomButton'"
      class="custom-action"
      @click="trigger(rows[0] || { name: widget.componentName })"
    >
      <i :class="iconClass(rows[0], 0)"></i
      ><span>{{ itemName(rows[0] || { name: widget.componentName }, 0) }}</span>
    </button>

    <section v-else-if="componentType === 'JTabs'" class="tabs-widget">
      <nav>
        <button
          v-for="(item, index) in rows.length
            ? rows
            : [{ name: '概览' }, { name: '明细' }, { name: '趋势' }]"
          :key="index"
          :class="{ active: activeTab === index }"
          @click="
            activeTab = index;
            trigger(item, index);
          "
        >
          {{ itemName(item, index) }}
        </button>
      </nav>
      <div class="tab-stage">
        <span></span>
        <div>
          <small>ACTIVE VIEW</small
          ><strong>{{
            itemName(
              (rows.length ? rows : [{ name: "概览" }])[activeTab] || {},
              activeTab,
            )
          }}</strong>
          <p>
            {{ itemDescription(rows[activeTab]) || "该区域可放置关联业务组件" }}
          </p>
        </div>
      </div>
    </section>

    <section v-else-if="componentType === 'JGrid'" class="grid-widget">
      <div v-for="index in 6" :key="index">
        <span>{{ String(index).padStart(2, "0") }}</span
        ><i></i>
      </div>
    </section>

    <section
      v-else-if="isMetricCards"
      class="metric-card-widget"
      :class="{
        compact: componentType === 'JSimpleCard',
        colorized: componentType === 'JColorBlock',
      }"
    >
      <article
        v-for="(item, index) in rows.slice(0, 6)"
        :key="index"
        :style="{ '--accent': colorAt(item, index) }"
        @click="trigger(item, index)"
      >
        <span><i :class="iconClass(item, index)"></i></span>
        <div>
          <small>{{ itemName(item, index) }}</small
          ><strong
            >{{ pick(item, ["prefix", "前缀"]) }}{{ formatValue(item)
            }}<em>{{
              pick(item, ["suffix", "unit", "单位", "后缀"])
            }}</em></strong
          >
          <p v-if="componentType === 'JGrowCard'">
            <i :style="{ width: `${progress(item)}%` }"></i>
          </p>
        </div>
      </article>
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-solid fa-chart-simple"></i><span>等待指标数据</span>
      </div>
    </section>

    <section v-else-if="componentType === 'JQuickNav'" class="quick-nav-widget">
      <button
        v-for="(item, index) in rows.slice(0, 8)"
        :key="index"
        :style="{ '--accent': colorAt(item, index) }"
        @click="trigger(item, index)"
      >
        <i :class="iconClass(item, index)"></i
        ><span>{{ itemName(item, index) }}</span
        ><b class="fa-solid fa-arrow-right"></b>
      </button>
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-solid fa-compass"></i><span>等待导航数据</span>
      </div>
    </section>

    <section
      v-else-if="componentType === 'JProjectCard'"
      class="project-widget"
    >
      <header>
        <span>项目进展</span><small>{{ rows.length }} 个项目</small>
      </header>
      <article
        v-for="(item, index) in rows.slice(0, 6)"
        :key="index"
        @click="trigger(item, index)"
      >
        <span :style="{ background: colorAt(item, index) }">{{
          initials(item, index)
        }}</span>
        <div>
          <strong>{{ itemName(item, index) }}</strong>
          <p>
            {{
              itemDescription(item) || pick(item, ["group", "分组"], "业务项目")
            }}
          </p>
        </div>
        <time>{{ itemTime(item) }}</time
        ><i class="fa-solid fa-chevron-right"></i>
      </article>
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-solid fa-diagram-project"></i><span>等待项目数据</span>
      </div>
    </section>

    <section v-else-if="componentType === 'JWaitMatter'" class="task-widget">
      <header>
        <span>待办事项</span><b>{{ rows.length }}</b>
      </header>
      <article
        v-for="(item, index) in rows.slice(0, 5)"
        :key="index"
        @click="trigger(item, index)"
      >
        <i :class="iconClass(item, index)"></i>
        <div>
          <strong>{{ itemName(item, index) }}</strong>
          <p>{{ itemDescription(item) }}</p>
        </div>
        <time>{{ itemTime(item) }}</time
        ><span></span>
      </article>
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-solid fa-clipboard-check"></i><span>等待待办数据</span>
      </div>
    </section>

    <section
      v-else-if="componentType === 'JDynamicInfo'"
      class="activity-widget"
    >
      <header>最新动态<small>LIVE FEED</small></header>
      <article
        v-for="(item, index) in rows.slice(0, 6)"
        :key="index"
        @click="trigger(item, index)"
      >
        <span :style="{ background: colorAt(item, index) }">{{
          initials(item, index)
        }}</span>
        <div>
          <strong>{{ itemName(item, index) }}</strong>
          <p>{{ itemDescription(item) }}</p>
        </div>
        <time>{{ itemTime(item) }}</time>
      </article>
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-solid fa-wave-square"></i><span>等待动态数据</span>
      </div>
    </section>

    <section v-else-if="isRankList" class="rank-widget">
      <article
        v-for="(item, index) in rows.slice(0, 8)"
        :key="index"
        @click="trigger(item, index)"
      >
        <b>{{ String(index + 1).padStart(2, "0") }}</b
        ><span>{{ itemName(item, index) }}</span
        ><i
          ><em
            :style="{
              width: `${Math.max(5, (numberValue(item) / maxRankValue) * 100)}%`,
              background: colorAt(item, index),
            }"
          ></em></i
        ><strong>{{ formatValue(item) }}</strong>
      </article>
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-solid fa-ranking-star"></i><span>等待排行数据</span>
      </div>
    </section>

    <section v-else-if="isRecordList" class="record-widget">
      <article
        v-for="(item, index) in rows.slice(0, 8)"
        :key="index"
        @click="trigger(item, index)"
      >
        <b>{{ String(index + 1).padStart(2, "0") }}</b>
        <div>
          <strong>{{ itemName(item, index) }}</strong>
          <p>{{ itemDescription(item) }}</p>
        </div>
        <time>{{ itemTime(item) }}</time>
      </article>
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-solid fa-list-ul"></i><span>等待列表数据</span>
      </div>
    </section>

    <section v-else-if="componentType === 'JDevHistory'" class="history-widget">
      <article v-for="(item, index) in rows.slice(0, 7)" :key="index">
        <time>{{
          pick(item, ["year", "date", "time", "年份"], `阶段 ${index + 1}`)
        }}</time
        ><i
          :style="{
            borderColor: colorAt(item, index),
            background: colorAt(item, index),
          }"
        ></i>
        <div>
          <strong>{{ itemName(item, index) }}</strong>
          <p>{{ itemDescription(item) }}</p>
        </div>
      </article>
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-solid fa-timeline"></i><span>等待历程数据</span>
      </div>
    </section>

    <section
      v-else-if="componentType === 'JWeatherForecast'"
      class="weather-widget"
    >
      <article v-for="(item, index) in rows.slice(0, 6)" :key="index">
        <small>{{
          pick(item, ["day", "date", "name"], `周${weekdays[index % 7]}`)
        }}</small
        ><i
          :class="
            pick(
              item,
              ['icon'],
              index % 3 ? 'fa-solid fa-cloud-sun' : 'fa-solid fa-sun',
            )
          "
        ></i
        ><strong>{{
          pick(item, ["temp", "temperature", "value"], "--°")
        }}</strong
        ><span>{{ pick(item, ["weather", "description"], "天气") }}</span>
      </article>
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-solid fa-cloud-sun"></i><span>等待天气数据</span>
      </div>
    </section>

    <section v-else-if="componentType === 'JCarousel'" class="image-carousel">
      <template v-if="rows.length">
        <img
          v-if="assetUrl(pick(rows[carouselIndex], ['url', 'src', 'imageUrl']))"
          :src="assetUrl(pick(rows[carouselIndex], ['url', 'src', 'imageUrl']))"
          :alt="itemDescription(rows[carouselIndex])"
        />
        <template v-if="rows.length > 1"
          ><button
            class="previous"
            aria-label="上一张"
            @click.stop="moveCarousel(-1)"
          >
            ‹</button
          ><button
            class="next"
            aria-label="下一张"
            @click.stop="moveCarousel(1)"
          >
            ›
          </button>
          <div class="carousel-dots">
            <button
              v-for="(_, index) in rows"
              :key="index"
              :aria-label="`第${index + 1}张`"
              :class="{ active: index === carouselIndex }"
              @click.stop="carouselIndex = index"
            /></div
        ></template>
      </template>
      <div v-else class="structured-empty">
        <i class="fa-regular fa-images" /><span>选择图片</span>
      </div>
    </section>
    <section v-else-if="isCarousel" class="carousel-widget">
      <article
        v-for="(item, index) in rows.slice(0, 4)"
        :key="index"
        :class="{ active: index === 0 }"
      >
        <img
          v-if="assetUrl(pick(item, ['url', 'src', 'imageUrl']))"
          :src="assetUrl(pick(item, ['url', 'src', 'imageUrl']))"
          alt=""
        /><span v-else><i :class="iconClass(item, index)"></i></span>
        <div>
          <strong>{{ itemName(item, index) }}</strong>
          <p>{{ itemDescription(item) }}</p>
        </div>
      </article>
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-regular fa-images"></i><span>等待轮播数据</span>
      </div>
    </section>

    <section v-else-if="isForm" class="form-widget">
      <label
        v-for="(field, index) in (rows.length ? rows : filterFields).slice(
          0,
          5,
        )"
        :key="index"
        ><span>{{
          pick(field, ["label", "name", "title"], `字段 ${index + 1}`)
        }}</span
        ><b>{{ pick(field, ["value", "placeholder"], "请输入") }}</b></label
      ><button @click="trigger({ name: '提交' })">提交</button>
    </section>

    <section v-else-if="componentType === 'JGender'" class="gender-widget">
      <article
        v-for="(item, index) in (rows.length
          ? rows
          : [
              { name: '女性', value: 0 },
              { name: '男性', value: 0 },
            ]
        ).slice(0, 2)"
        :key="index"
        :class="index ? 'male' : 'female'"
      >
        <i :class="index ? 'fa-solid fa-mars' : 'fa-solid fa-venus'"></i
        ><strong>{{ formatValue(item) }}</strong
        ><span>{{ itemName(item, index) }}</span>
      </article>
    </section>

    <section v-else-if="componentType === 'JOrbitRing'" class="orbit-widget">
      <div class="orbit-core">
        <span></span><strong>{{ widget.componentName }}</strong>
      </div>
      <i
        v-for="(item, index) in rows.slice(0, 8)"
        :key="index"
        :style="{
          '--index': index,
          '--total': Math.max(1, rows.slice(0, 8).length),
          '--accent': colorAt(item, index),
        }"
        ><b>{{ initials(item, index) }}</b
        ><em>{{ itemName(item, index) }}</em></i
      >
    </section>

    <section v-else-if="componentType === 'JFlashCloud'" class="cloud-widget">
      <span
        v-for="(item, index) in rows.slice(0, 28)"
        :key="index"
        :style="{
          color: colorAt(item, index),
          fontSize: `${14 + Math.min(24, (numberValue(item) / Math.max(1, maxRankValue)) * 24)}px`,
        }"
        >{{ itemName(item, index) }}</span
      >
      <div v-if="!rows.length" class="structured-empty">
        <i class="fa-solid fa-cloud"></i><span>等待词云数据</span>
      </div>
    </section>

    <section
      v-else-if="['JImg', 'JCustomIcon'].includes(componentType)"
      class="media-widget"
    >
      <AuthorizedImage
        v-if="widget.config?.file_id"
        :file-id="widget.config.file_id"
        :alt="widget.componentName"
        :fit="option.fit"
      />
      <img
        v-else-if="imageUrl"
        :src="imageUrl"
        :alt="widget.componentName"
        :style="{ objectFit: option.fit || 'contain' }"
      /><i
        v-else-if="componentType === 'JCustomIcon'"
        class="custom-material-icon"
        :class="option.icon || 'fa-solid fa-star'"
        :style="{ color: option.iconColor || '#38bdf8' }"
      />
      <div v-else class="structured-empty">
        <i class="fa-regular fa-image"></i><span>选择图片</span>
      </div>
    </section>

    <section
      v-else-if="componentType === 'JCommon'"
      class="common-widget"
      :class="{
        gantt: /甘特/.test(widget.componentName),
        elements: /五行/.test(widget.componentName),
        mascot: /牛气/.test(widget.componentName),
      }"
    >
      <template v-if="/甘特/.test(widget.componentName)"
        ><header>
          <span>任务</span><b v-for="month in 6" :key="month">M{{ month }}</b>
        </header>
        <article
          v-for="(item, index) in (rows.length
            ? rows
            : [{ name: '等待任务数据' }]
          ).slice(0, 6)"
          :key="index"
        >
          <strong>{{ itemName(item, index) }}</strong
          ><i
            ><em
              :style="{
                left: ganttLeft(item, index),
                width: ganttWidth(item),
                background: colorAt(item, index),
              }"
            ></em
          ></i></article
      ></template>
      <template v-else-if="/五行/.test(widget.componentName)"
        ><div class="element-core">业务</div>
        <i
          v-for="(name, index) in ['金', '木', '水', '火', '土']"
          :key="name"
          :style="{ '--index': index }"
          ><b>{{ name }}</b></i
        ></template
      >
      <template v-else-if="/牛气/.test(widget.componentName)"
        ><div class="mascot-mark">
          <i class="fa-solid fa-arrow-trend-up"></i>
        </div>
        <strong>增长势能</strong><small>BUSINESS MOMENTUM</small></template
      >
      <template v-else
        ><div class="common-core">
          <i></i><span></span
          ><b>{{ rows.length ? formatValue(rows[0]) : "DATA" }}</b
          ><small>{{ widget.componentName }}</small>
        </div></template
      >
    </section>
  </div>
</template>

<style scoped>
.business-widget {
  width: 100%;
  height: 100%;
  overflow: hidden;
  color: #dcecff;
  font-family: Inter, "Microsoft YaHei", sans-serif;
}
.business-widget * {
  box-sizing: border-box;
}
button {
  font: inherit;
}
.structured-empty {
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  min-height: 100%;
  color: #6f91ad;
  font-size: 12px;
}
.structured-empty i {
  color: #34b7ef;
  font-size: 25px;
}

.calendar-widget {
  height: 100%;
  padding: 12px;
  border: 1px solid rgba(89, 170, 230, 0.24);
  background: linear-gradient(
    150deg,
    rgba(12, 42, 71, 0.94),
    rgba(5, 21, 39, 0.94)
  );
}
.calendar-widget > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 50px;
  border-bottom: 1px solid rgba(101, 175, 225, 0.18);
}
.calendar-widget header div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.calendar-widget header small {
  color: #559bc7;
  font-size: 8px;
  letter-spacing: 0.18em;
}
.calendar-widget header strong {
  font-size: 17px;
  font-weight: 600;
}
.calendar-widget header > span {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #7c9eb8;
  font-size: 10px;
}
.calendar-widget header b {
  padding: 5px 9px;
  border: 1px solid rgba(64, 174, 235, 0.28);
  color: #aee6ff;
  font-weight: 500;
}
.calendar-layout {
  display: flex;
  height: calc(100% - 50px);
}
.calendar-month {
  flex: 1;
  min-width: 0;
}
.calendar-week,
.calendar-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
}
.calendar-week {
  height: 31px;
  border-bottom: 1px solid rgba(101, 175, 225, 0.14);
}
.calendar-week b {
  display: grid;
  place-items: center;
  color: #6f94b0;
  font-size: 10px;
  font-weight: 500;
}
.calendar-grid {
  height: calc(100% - 31px);
  grid-template-rows: repeat(6, 1fr);
}
.calendar-grid div {
  position: relative;
  display: grid;
  place-items: center;
  border-right: 1px solid rgba(101, 175, 225, 0.09);
  border-bottom: 1px solid rgba(101, 175, 225, 0.09);
  color: #bad2e4;
  font-size: 11px;
}
.calendar-grid .muted {
  color: #3f5b70;
}
.calendar-grid .today span {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #168ed0;
  color: #fff;
  box-shadow: 0 0 18px rgba(22, 142, 208, 0.52);
}
.calendar-grid .busy i {
  position: absolute;
  bottom: 4px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #35e4c6;
  box-shadow: 0 0 7px #35e4c6;
}
.calendar-layout aside {
  width: 34%;
  padding: 13px 0 0 12px;
  border-left: 1px solid rgba(101, 175, 225, 0.14);
  overflow: hidden;
}
.calendar-layout aside > small {
  color: #6ca6ca;
  font-size: 9px;
  letter-spacing: 0.1em;
}
.calendar-layout aside button {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 0;
  border: 0;
  border-bottom: 1px solid rgba(101, 175, 225, 0.1);
  background: transparent;
  color: #dcecff;
  text-align: left;
}
.calendar-layout aside button > i {
  width: 3px;
  height: 25px;
}
.calendar-layout aside button span {
  min-width: 0;
}
.calendar-layout aside button b,
.calendar-layout aside button em {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.calendar-layout aside button b {
  font-size: 10px;
}
.calendar-layout aside button em {
  margin-top: 3px;
  color: #64859e;
  font-size: 8px;
  font-style: normal;
}
.calendar-layout aside p {
  color: #57738a;
  font-size: 10px;
}

.filter-widget {
  display: flex;
  align-items: flex-end;
  gap: 9px;
  height: 100%;
  padding: 12px 14px;
  border: 1px solid rgba(82, 163, 218, 0.28);
  background: rgba(8, 29, 51, 0.92);
}
.filter-widget label {
  flex: 1;
  min-width: 0;
}
.filter-widget label > span:first-child {
  display: block;
  margin-bottom: 5px;
  color: #7998af;
  font-size: 9px;
}
.filter-control {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 31px;
  padding: 0 10px;
  border: 1px solid #294a63;
  background: #091d30;
  color: #7894aa;
  font-size: 10px;
}
.filter-widget > button {
  height: 31px;
  padding: 0 14px;
  border: 1px solid #198dca;
  background: #147bb1;
  color: white;
  font-size: 10px;
}
.filter-widget > button i {
  margin-right: 6px;
}
.filter-widget > button.secondary {
  border-color: #31536c;
  background: transparent;
  color: #91adc1;
}
.custom-action {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  width: 100%;
  height: 100%;
  border: 1px solid #28a8e8;
  border-radius: 3px;
  background: linear-gradient(135deg, #167eb5, #0e5d91);
  color: #fff;
  box-shadow:
    inset 0 1px rgba(255, 255, 255, 0.18),
    0 0 18px rgba(26, 145, 205, 0.22);
}

.tabs-widget {
  height: 100%;
  border: 1px solid #274861;
  background: #071b2d;
}
.tabs-widget nav {
  display: flex;
  height: 38px;
  border-bottom: 1px solid #274861;
  background: #0b2238;
}
.tabs-widget nav button {
  min-width: 92px;
  padding: 0 17px;
  border: 0;
  border-right: 1px solid #274861;
  background: transparent;
  color: #7698b1;
  font-size: 11px;
}
.tabs-widget nav button.active {
  color: #dff6ff;
  background: #103653;
  box-shadow: inset 0 -2px #31baf5;
}
.tab-stage {
  position: relative;
  display: grid;
  place-items: center;
  height: calc(100% - 38px);
  overflow: hidden;
}
.tab-stage > span {
  position: absolute;
  width: 58%;
  aspect-ratio: 1;
  border: 1px dashed rgba(59, 181, 235, 0.22);
  border-radius: 50%;
}
.tab-stage > div {
  z-index: 1;
  text-align: center;
}
.tab-stage small {
  color: #3a8db7;
  font-size: 8px;
  letter-spacing: 0.2em;
}
.tab-stage strong {
  display: block;
  margin: 8px 0;
  font-size: 22px;
}
.tab-stage p {
  color: #65859d;
  font-size: 10px;
}
.grid-widget {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 9px;
  height: 100%;
  padding: 10px;
  border: 1px solid #274861;
  background: #071b2d;
}
.grid-widget div {
  position: relative;
  border: 1px dashed rgba(74, 170, 226, 0.38);
  background: rgba(21, 72, 106, 0.16);
}
.grid-widget span {
  position: absolute;
  top: 7px;
  left: 8px;
  color: #477f9f;
  font-size: 9px;
}
.grid-widget i {
  position: absolute;
  inset: 36% 43%;
  border: 1px solid #2f91c8;
  transform: rotate(45deg);
}

.metric-card-widget {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 8px;
  height: 100%;
  padding: 8px;
  background: rgba(5, 20, 35, 0.88);
}
.metric-card-widget article {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
  padding: 11px;
  border: 1px solid color-mix(in srgb, var(--accent) 42%, transparent);
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--accent) 14%, transparent),
    rgba(8, 28, 47, 0.88)
  );
  box-shadow: inset 2px 0 var(--accent);
}
.metric-card-widget article > span {
  display: grid;
  flex: 0 0 34px;
  place-items: center;
  height: 34px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  color: var(--accent);
}
.metric-card-widget article > div {
  min-width: 0;
  flex: 1;
}
.metric-card-widget small {
  display: block;
  overflow: hidden;
  color: #88a8bd;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.metric-card-widget strong {
  display: block;
  margin-top: 5px;
  color: #eefaff;
  font-size: 18px;
  font-weight: 600;
}
.metric-card-widget strong em {
  margin-left: 2px;
  color: #7799b0;
  font-size: 8px;
  font-style: normal;
}
.metric-card-widget p {
  height: 3px;
  margin: 7px 0 0;
  background: rgba(108, 151, 180, 0.15);
}
.metric-card-widget p i {
  display: block;
  height: 100%;
  background: var(--accent);
  box-shadow: 0 0 7px var(--accent);
}
.metric-card-widget.compact article {
  padding: 8px;
}
.metric-card-widget.colorized article {
  box-shadow:
    inset 3px 0 var(--accent),
    0 8px 22px rgba(0, 0, 0, 0.14);
}
.quick-nav-widget {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(92px, 1fr));
  gap: 8px;
  height: 100%;
  padding: 8px;
  background: #071b2d;
}
.quick-nav-widget button {
  position: relative;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 7px;
  border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
  background: linear-gradient(
    145deg,
    color-mix(in srgb, var(--accent) 11%, transparent),
    rgba(8, 29, 50, 0.9)
  );
  color: #cfe4f3;
}
.quick-nav-widget button > i {
  color: var(--accent);
  font-size: 20px;
}
.quick-nav-widget button span {
  font-size: 9px;
}
.quick-nav-widget button b {
  position: absolute;
  top: 7px;
  right: 7px;
  color: color-mix(in srgb, var(--accent) 60%, transparent);
  font-size: 7px;
}

.project-widget,
.task-widget,
.activity-widget {
  height: 100%;
  padding: 9px 12px;
  background: linear-gradient(160deg, #0a263e, #061827);
}
.project-widget header,
.task-widget header,
.activity-widget header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 35px;
  border-bottom: 1px solid rgba(86, 165, 215, 0.18);
  font-size: 12px;
  font-weight: 600;
}
.project-widget header small,
.activity-widget header small {
  color: #4f8eb4;
  font-size: 8px;
  letter-spacing: 0.12em;
}
.task-widget header b {
  display: grid;
  place-items: center;
  min-width: 21px;
  height: 21px;
  border-radius: 50%;
  background: #1687c3;
  font-size: 9px;
}
.project-widget article,
.task-widget article,
.activity-widget article {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 43px;
  border-bottom: 1px solid rgba(86, 165, 215, 0.1);
}
.project-widget article > span,
.activity-widget article > span {
  display: grid;
  flex: 0 0 28px;
  place-items: center;
  height: 28px;
  border-radius: 5px;
  color: #fff;
  font-size: 10px;
}
.project-widget article > div,
.task-widget article > div,
.activity-widget article > div {
  min-width: 0;
  flex: 1;
}
.project-widget strong,
.task-widget strong,
.activity-widget strong {
  display: block;
  overflow: hidden;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-widget p,
.task-widget p,
.activity-widget p {
  overflow: hidden;
  margin: 3px 0 0;
  color: #64859d;
  font-size: 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-widget time,
.task-widget time,
.activity-widget time {
  color: #5b7a91;
  font-size: 8px;
}
.project-widget article > i {
  color: #3e7597;
  font-size: 8px;
}
.task-widget article > i {
  width: 22px;
  color: #39bce9;
  text-align: center;
}
.task-widget article > span {
  width: 6px;
  height: 6px;
  border: 1px solid #42d8bc;
  border-radius: 50%;
}

.rank-widget,
.record-widget {
  height: 100%;
  padding: 8px 12px;
  background: #071b2d;
}
.rank-widget article {
  display: grid;
  grid-template-columns: 27px minmax(62px, 0.8fr) 1fr 52px;
  align-items: center;
  gap: 8px;
  height: calc(100% / max(1, var(--row-count, 6)));
  min-height: 30px;
  border-bottom: 1px solid rgba(83, 157, 205, 0.1);
}
.rank-widget article > b {
  color: #4d87a8;
  font-size: 10px;
}
.rank-widget article > span {
  overflow: hidden;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rank-widget article > i {
  height: 4px;
  background: rgba(94, 141, 174, 0.16);
}
.rank-widget article > i em {
  display: block;
  height: 100%;
  box-shadow: 0 0 7px currentColor;
}
.rank-widget article > strong {
  color: #a8d9ef;
  font-size: 10px;
  text-align: right;
}
.record-widget article {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 36px;
  border-bottom: 1px solid rgba(83, 157, 205, 0.1);
}
.record-widget article > b {
  color: #3fbbe8;
  font-size: 9px;
}
.record-widget article > div {
  flex: 1;
  min-width: 0;
}
.record-widget strong,
.record-widget p {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.record-widget strong {
  font-size: 10px;
}
.record-widget p {
  margin: 3px 0 0;
  color: #617f96;
  font-size: 8px;
}
.record-widget time {
  color: #50728a;
  font-size: 8px;
}
.history-widget {
  position: relative;
  height: 100%;
  padding: 12px;
  background: #071b2d;
}
.history-widget::before {
  position: absolute;
  top: 15px;
  bottom: 15px;
  left: 76px;
  width: 1px;
  background: #26506d;
  content: "";
}
.history-widget article {
  position: relative;
  display: grid;
  grid-template-columns: 53px 14px 1fr;
  gap: 10px;
  align-items: start;
  min-height: 41px;
}
.history-widget time {
  color: #5a839e;
  font-size: 8px;
  text-align: right;
}
.history-widget article > i {
  z-index: 1;
  width: 8px;
  height: 8px;
  margin: 2px 0 0 3px;
  border: 2px solid;
  border-radius: 50%;
  box-shadow: 0 0 9px currentColor;
}
.history-widget strong {
  display: block;
  font-size: 10px;
}
.history-widget p {
  overflow: hidden;
  margin: 3px 0 0;
  color: #607d93;
  font-size: 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.weather-widget {
  display: flex;
  align-items: stretch;
  height: 100%;
  padding: 7px;
  background: linear-gradient(145deg, #0b3450, #071b2d);
}
.weather-widget article {
  display: grid;
  flex: 1;
  place-items: center;
  align-content: center;
  gap: 5px;
  border-right: 1px solid rgba(112, 183, 225, 0.16);
}
.weather-widget article:last-child {
  border: 0;
}
.weather-widget small,
.weather-widget span {
  color: #6f91a8;
  font-size: 8px;
}
.weather-widget article > i {
  color: #55c6ef;
  font-size: 18px;
}
.weather-widget strong {
  font-size: 13px;
}
.carousel-widget {
  display: flex;
  gap: 8px;
  height: 100%;
  padding: 8px;
  background: #071b2d;
}
.carousel-widget article {
  position: relative;
  flex: 0.7;
  min-width: 0;
  overflow: hidden;
  border: 1px solid #244762;
  background: #0c2a43;
  opacity: 0.7;
}
.carousel-widget article.active {
  flex: 1.5;
  border-color: #2b9ed4;
  opacity: 1;
}
.carousel-widget img,
.carousel-widget article > span {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.carousel-widget article > span {
  display: grid;
  place-items: center;
  color: #35bce9;
  font-size: 32px;
}
.carousel-widget article > div {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  padding: 17px 10px 8px;
  background: linear-gradient(transparent, rgba(2, 12, 21, 0.94));
}
.carousel-widget strong,
.carousel-widget p {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.carousel-widget strong {
  font-size: 10px;
}
.carousel-widget p {
  margin: 3px 0 0;
  color: #86a5ba;
  font-size: 8px;
}
.form-widget {
  display: flex;
  align-items: flex-end;
  gap: 9px;
  height: 100%;
  padding: 10px;
  border: 1px solid #294b64;
  background: #081d30;
}
.form-widget label {
  flex: 1;
  min-width: 0;
}
.form-widget label span {
  display: block;
  margin-bottom: 4px;
  color: #7a9aaf;
  font-size: 8px;
}
.form-widget label b {
  display: block;
  overflow: hidden;
  height: 30px;
  padding: 0 9px;
  border: 1px solid #2b4d66;
  background: #061726;
  color: #69869a;
  font-size: 9px;
  font-weight: 400;
  line-height: 28px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.form-widget button {
  height: 30px;
  padding: 0 17px;
  border: 1px solid #289bd3;
  background: #1679ab;
  color: #fff;
  font-size: 9px;
}
.gender-widget {
  display: flex;
  height: 100%;
}
.gender-widget article {
  display: grid;
  flex: 1;
  place-items: center;
  align-content: center;
  gap: 6px;
  background: linear-gradient(
    145deg,
    rgba(236, 92, 168, 0.18),
    rgba(9, 28, 46, 0.96)
  );
}
.gender-widget article.male {
  background: linear-gradient(
    145deg,
    rgba(66, 158, 232, 0.2),
    rgba(9, 28, 46, 0.96)
  );
}
.gender-widget i {
  color: #ef72b3;
  font-size: 28px;
}
.gender-widget .male i {
  color: #55aaf0;
}
.gender-widget strong {
  font-size: 22px;
}
.gender-widget span {
  color: #82a0b5;
  font-size: 9px;
}

.orbit-widget {
  position: relative;
  display: grid;
  place-items: center;
  height: 100%;
  overflow: hidden;
  background: radial-gradient(
    circle,
    rgba(22, 119, 177, 0.25),
    rgba(5, 18, 31, 0.95) 65%
  );
}
.orbit-widget::before,
.orbit-widget::after {
  position: absolute;
  width: 64%;
  aspect-ratio: 1;
  border: 1px dashed rgba(57, 185, 231, 0.32);
  border-radius: 50%;
  content: "";
}
.orbit-widget::after {
  width: 42%;
}
.orbit-core {
  z-index: 2;
  display: grid;
  place-items: center;
  align-content: center;
  width: 23%;
  aspect-ratio: 1;
  border: 1px solid #39b7e7;
  border-radius: 50%;
  background: #0b3a59;
  box-shadow: 0 0 25px rgba(49, 179, 226, 0.3);
}
.orbit-core span {
  width: 16px;
  height: 3px;
  background: #31d8c1;
  box-shadow: 0 0 8px #31d8c1;
}
.orbit-core strong {
  margin-top: 7px;
  font-size: 10px;
}
.orbit-widget > i {
  --angle: calc(360deg / var(--total) * var(--index));
  position: absolute;
  top: calc(50% - 17px);
  left: calc(50% - 17px);
  width: 34px;
  height: 34px;
  transform: rotate(var(--angle)) translateX(min(180px, 14vw))
    rotate(calc(-1 * var(--angle)));
}
.orbit-widget > i b {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--accent);
  border-radius: 50%;
  background: #08253b;
  color: var(--accent);
  font-size: 10px;
  font-style: normal;
}
.orbit-widget > i em {
  display: block;
  width: 70px;
  margin-left: -18px;
  color: #7798ad;
  font-size: 8px;
  font-style: normal;
  text-align: center;
}
.cloud-widget {
  display: flex;
  align-content: center;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px 14px;
  height: 100%;
  padding: 14px;
  background: radial-gradient(
    circle,
    rgba(22, 119, 177, 0.18),
    rgba(5, 18, 31, 0.94)
  );
}
.cloud-widget > span {
  text-shadow: 0 0 12px currentColor;
}
.media-widget {
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  background: rgba(5, 18, 31, 0.66);
}
.media-widget img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.custom-material-icon {
  font-size: clamp(24px, 5vw, 80px);
}
.image-carousel {
  position: relative;
  width: 100%;
  height: 100%;
  background: rgba(5, 18, 31, 0.66);
}
.image-carousel > img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.image-carousel > button {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 32px;
  height: 40px;
  background: #071b2d99;
  border: 0;
  color: #fff;
  font-size: 30px;
  cursor: pointer;
}
.image-carousel .previous {
  left: 6px;
}
.image-carousel .next {
  right: 6px;
}
.carousel-dots {
  position: absolute;
  bottom: 10px;
  left: 0;
  width: 100%;
  display: flex;
  justify-content: center;
  gap: 7px;
}
.carousel-dots button {
  width: 8px;
  height: 8px;
  padding: 0;
  border: 1px solid #cce9ff;
  border-radius: 50%;
  background: transparent;
}
.carousel-dots button.active {
  background: #59caff;
}

.common-widget {
  position: relative;
  height: 100%;
  overflow: hidden;
  background: radial-gradient(
    circle,
    rgba(18, 126, 191, 0.28),
    rgba(4, 17, 30, 0.97)
  );
}
.common-widget.gantt {
  padding: 10px;
}
.common-widget.gantt header,
.common-widget.gantt article {
  display: grid;
  grid-template-columns: 86px repeat(6, 1fr);
  align-items: center;
  min-height: 34px;
  border-bottom: 1px solid rgba(75, 153, 204, 0.13);
}
.common-widget.gantt header span,
.common-widget.gantt header b {
  color: #5685a2;
  font-size: 8px;
  font-weight: 500;
  text-align: center;
}
.common-widget.gantt article strong {
  overflow: hidden;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.common-widget.gantt article > i {
  position: relative;
  grid-column: 2 / -1;
  height: 12px;
  background: rgba(73, 128, 163, 0.12);
}
.common-widget.gantt article > i em {
  position: absolute;
  height: 100%;
  border-radius: 2px;
  box-shadow: 0 0 8px currentColor;
}
.common-widget.elements {
  display: grid;
  place-items: center;
}
.element-core {
  display: grid;
  place-items: center;
  width: 72px;
  height: 72px;
  border: 1px solid #35c8df;
  border-radius: 50%;
  background: #0b4361;
  box-shadow: 0 0 32px rgba(50, 199, 222, 0.3);
}
.common-widget.elements > i {
  --angle: calc(var(--index) * 72deg);
  position: absolute;
  top: calc(50% - 22px);
  left: calc(50% - 22px);
  width: 44px;
  height: 44px;
  transform: rotate(var(--angle)) translateX(min(180px, 15vw))
    rotate(calc(-1 * var(--angle)));
}
.common-widget.elements > i b {
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  border: 1px solid #3d96c3;
  border-radius: 50%;
  background: #092c45;
  color: #bdefff;
  font-size: 14px;
  font-style: normal;
}
.common-widget.mascot {
  display: grid;
  place-items: center;
  align-content: center;
}
.mascot-mark {
  display: grid;
  place-items: center;
  width: 88px;
  height: 88px;
  border: 1px solid #31dfb7;
  border-radius: 26% 74% 63% 37% / 54% 36% 64% 46%;
  background: linear-gradient(145deg, #125e70, #0a293f);
  color: #43ecc4;
  font-size: 38px;
  box-shadow: 0 0 38px rgba(49, 223, 183, 0.25);
  transform: rotate(12deg);
}
.mascot-mark i {
  transform: rotate(-12deg);
}
.common-widget.mascot > strong {
  margin-top: 13px;
  font-size: 18px;
}
.common-widget.mascot > small {
  margin-top: 4px;
  color: #4d91a9;
  font-size: 8px;
  letter-spacing: 0.18em;
}
.common-core {
  display: grid;
  place-items: center;
  align-content: center;
  height: 100%;
}
.common-core > i,
.common-core > span {
  position: absolute;
  width: 46%;
  aspect-ratio: 1;
  border: 1px dashed rgba(50, 185, 222, 0.32);
  border-radius: 50%;
}
.common-core > span {
  width: 28%;
  border-style: solid;
}
.common-core b {
  z-index: 1;
  color: #7de8ff;
  font-size: 25px;
}
.common-core small {
  z-index: 1;
  margin-top: 7px;
  color: #5891aa;
  font-size: 9px;
}
</style>
