/** Test5展示分支的类型与字段协议；仅消费服务端可信投影，不推断业务数值。 */
export const tableTypes = [
  "JCommonTable",
  "JScrollTable",
  "JScrollRankingBoard",
];
export const rankTypes = ["JRankingList", "JFlashList"];
export const listTypes = ["JList", "JScrollList"];
export const cardTypes = ["JGrowCard", "JSimpleCard", "JColorBlock"];
export const numberTypes = ["JNumber", "JCountTo"];
export const progressTypes = [
  "JProgress",
  "JCustomProgress",
  "JListProgress",
  "JTotalProgress",
  "JRingProgress",
  "JRoundProgress",
];
export const specialChartTypes = [
  "JFunnel",
  "JPyramidFunnel",
  "JRose",
  "JSemiGauge",
  "JCircleRadar",
  "JRectangle",
];
export const staticTypes = ["JCurrentTime", "JDragEditor"];
export const presentationTypes = [
  ...tableTypes,
  ...rankTypes,
  ...listTypes,
  ...cardTypes,
  ...numberTypes,
  ...progressTypes,
  ...specialChartTypes,
  ...staticTypes,
];
export interface PresentationRow {
  name: string;
  value: string | null;
  description: string;
  time: string;
  unit: string;
  percentage?: string | null;
  total?: string;
}
export interface PresentationModel {
  status: "ready" | "empty" | "needs_mapping" | "invalid_data";
  message?: string;
  component: string;
  rows: PresentationRow[];
  columns: { field: string; label: string }[];
  maximum?: string;
}

/** 精提原JDragEditor的安全富文本用途，只保留基础排版，与服务端白名单一致。 */
export function safeRichText(value: unknown) {
  const allowed = new Set([
    "P",
    "BR",
    "STRONG",
    "B",
    "EM",
    "I",
    "U",
    "S",
    "UL",
    "OL",
    "LI",
    "H1",
    "H2",
    "H3",
    "BLOCKQUOTE",
    "PRE",
    "CODE",
    "SPAN",
    "TABLE",
    "THEAD",
    "TBODY",
    "TR",
    "TH",
    "TD",
  ]);
  const blocked = new Set([
    "SCRIPT",
    "STYLE",
    "IFRAME",
    "OBJECT",
    "SVG",
    "MATH",
    "TEMPLATE",
    "VIDEO",
    "AUDIO",
  ]);
  const doc = new DOMParser().parseFromString(
    String(value ?? "").slice(0, 100000),
    "text/html",
  );
  const clean = (parent: Element) => {
    for (const child of [...parent.children]) {
      if (blocked.has(child.tagName)) {
        child.remove();
        continue;
      }
      clean(child);
      for (const attribute of [...child.attributes])
        child.removeAttribute(attribute.name);
      if (!allowed.has(child.tagName)) child.replaceWith(...child.childNodes);
    }
  };
  clean(doc.body);
  return doc.body.innerHTML;
}
