/**
 * businessWidgetTypes —— 大屏"业务类组件"类型白名单。
 *
 * 作用：JimuWidgetRenderer 用它把业务组件（日历/卡片/列表/导航等）从图表与表格分支中分流出去，
 * 交给 JimuBusinessWidget 渲染。
 * 关键约束：
 * - 组件类型名必须与 data/jimuComponentLibrary.json 中的 compType 完全一致（大小写敏感）；
 * - 新增业务组件时需同时补充 JimuBusinessWidget 的渲染分支，否则会落到默认空态；
 * - 本文件无副作用、无外部依赖，可安全用于渲染期判定。
 */
export const BUSINESS_WIDGET_TYPES = Object.freeze([
  'JCalendar',
  'JCardCarousel',
  'JCardScroll',
  'JCarousel',
  'JColorBlock',
  'JCommon',
  'JCustomButton',
  'JCustomIcon',
  'JDevHistory',
  'JDynamicInfo',
  'JFilterQuery',
  'JFlashCloud',
  'JFlashList',
  'JForm',
  'JGender',
  'JGrid',
  'JGrowCard',
  'JImg',
  'JList',
  'JMultiViewCalendar',
  'JOrbitRing',
  'JPermanentCalendar',
  'JProjectCard',
  'JQuickNav',
  'JRankingList',
  'JScrollList',
  'JSimpleCard',
  'JTabs',
  'JWaitMatter',
  'JWeatherForecast',
  'design',
  'online',
])

const BUSINESS_WIDGET_TYPE_SET = new Set(BUSINESS_WIDGET_TYPES)

/**
 * 判定组件是否为业务类组件。
 * @param {string} componentType 组件的 compType 名称
 * @returns {boolean} 命中白名单返回 true；空值或非字符串一律返回 false
 */
export function isBusinessWidgetType(componentType) {
  return BUSINESS_WIDGET_TYPE_SET.has(String(componentType || ''))
}
