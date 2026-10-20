/** 按设计坐标中的真实槽位适配快照文字，避免小模板沿用大画布字号后裁字。 */
export function fitSnapshotFont(
  text,
  width,
  height,
  requested = 20,
  lineHeight = 1.2,
  letterSpacing = 0,
) {
  const lines = String(text ?? "").split("\n");
  const units = (value) =>
    [...value].reduce(
      (sum, char) => sum + (/[^\u0000-\u00ff]/.test(char) ? 1 : 0.64),
      0,
    );
  const longest = Math.max(1, ...lines.map(units));
  const gaps = Math.max(0, ...lines.map((line) => [...line].length - 1));
  const availableWidth = Math.max(
    1,
    Number(width) - gaps * Number(letterSpacing || 0),
  );
  return Math.max(
    1,
    Math.min(
      Number(requested) || 20,
      availableWidth / longest,
      Math.max(1, Number(height)) / (Math.max(1, lines.length) * lineHeight),
    ),
  );
}

/** 长编码保留有区别的段与末尾，不把真实部门编码改名为虚构的部门序号。 */
export function snapshotCategoryLabel(value, compact = false) {
  const text = String(value ?? "未提供");
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{12,}$/.test(text)) return text;
  const segments = text.split("-");
  const distinct = segments.length > 1 ? segments.slice(1).join("-") : text;
  return `${distinct.slice(0, compact ? 2 : 4)}…${distinct.slice(-4)}`;
}

/** 金额只加千分位与移除多余尾零，不经浮点运算截断原始小数精度。 */
export function exactSnapshotNumber(value) {
  const text = String(value ?? "");
  if (!/^-?\d+(?:\.\d+)?$/.test(text)) return value;
  const [integer, fraction = ""] = text.split(".");
  const decimals = fraction.replace(/0+$/, "");
  return (
    integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",") +
    (decimals ? `.${decimals}` : "")
  );
}

/** 旧快照把来源前缀重复写进指标标题，卡片只显示末尾字段名，原名仍用于悬停。 */
export function snapshotMetricLabel(value) {
  const parts = String(value || "").split(/\s+·\s+/);
  return parts[parts.length - 1].trim();
}

/** 仅把快照报表唯一剩余的空业务槽用于真实数据口径说明，不把说明复制成伪业务图。 */
export function snapshotScopeContext(widgets, description = "") {
  const saved = widgets.filter(
    (widget) => widget.config?.snapshotOnly && widget.config?.queryId,
  );
  const queryCount = new Set(saved.map((widget) => widget.config.queryId)).size;
  const scopeNote = String(description || "").trim();
  const empty = widgets.filter((widget) => {
    if (
      widget.visible === false ||
      widget.config?.queryId ||
      widget.config?.dataStatus === "verified"
    )
      return false;
    const businessSlot =
      /^(metric|table|bar|hbar|line|area|pie|scatter|radar|gauge|funnel|map)$/.test(
        widget.type || "",
      ) || /Table|ScrollBoard|RankingBoard/.test(widget.component || "");
    if (!businessSlot) return false;
    let rows =
      widget.config?.chartDataParsed ?? widget.config?.chartData ?? widget.data;
    if (typeof rows === "string") {
      try {
        rows = JSON.parse(rows);
      } catch {
        return false;
      }
    }
    return Array.isArray(rows) && rows.length === 0;
  });
  return {
    queryCount,
    scopeNote: queryCount && scopeNote && empty.length === 1 ? scopeNote : "",
    scopeNoteWidgetId:
      queryCount && scopeNote && empty.length === 1 ? String(empty[0].id) : "",
  };
}
