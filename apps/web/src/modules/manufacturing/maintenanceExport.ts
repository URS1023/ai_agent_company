/** 复用现有CSV下载方式；只处理浏览器文件名与临时链接，不拼装或重算分析数据。 */
export function maintenanceCsvName(name: unknown): string {
  const base = (typeof name === "string" ? name : "")
    .trim()
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_")
    .replace(/[. ]+$/, "")
    .slice(0, 120);
  return `${base || "维护分析"}-分组数据.csv`;
}

export function downloadMaintenanceCsv(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  let clicked = false;
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    clicked = true;
  } finally {
    // 成功点击后稍延释放，失败立即回收，不留下可重复访问的临时链接。
    if (clicked) window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    else URL.revokeObjectURL(url);
  }
}
