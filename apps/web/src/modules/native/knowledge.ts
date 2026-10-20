/** 原生知识返回值保留索引状态与来源映射，不把已提交当成已完成。 */
export interface KnowledgeDocument {
  id: string;
  name: string;
  indexing_status?: string;
  display_status?: string;
  created_at?: number;
  word_count?: number;
  error?: string;
  enabled?: boolean;
  archived?: boolean;
}
export interface DocumentSource {
  id: string;
  file_id: string;
  name: string;
  source_revision?: number;
  source_sha256?: string;
}
export interface KnowledgeCitation {
  id?: string;
  document_name?: string;
  position?: number;
  content?: string;
  score?: number | string;
}
export const textExtensions = [".txt", ".md", ".csv", ".json", ".log"];
export function importFileError(file: { name?: string; size?: number }) {
  const name = (file.name ?? "").toLowerCase();
  if (!textExtensions.some((extension) => name.endsWith(extension)))
    return "请选择文本、Markdown、CSV、JSON或日志文件";
  if (!file.size || file.size > 2 * 1024 * 1024)
    return "请选择2MB以内的非空文件";
  return "";
}
const statuses: Record<string, string> = {
  waiting: "排队中",
  queuing: "排队中",
  parsing: "解析中",
  cleaning: "清洗中",
  splitting: "分段中",
  indexing: "索引中",
  completed: "可检索",
  available: "可检索",
  error: "处理失败",
  failed: "处理失败",
  paused: "已暂停",
  stopped: "已停止",
  disabled: "已停用",
  archived: "已归档",
};
export function documentStatus(document: KnowledgeDocument) {
  const status = document.archived
    ? "archived"
    : document.enabled === false
      ? "disabled"
      : ["paused", "disabled", "archived"].includes(
            document.display_status ?? "",
          )
        ? document.display_status!
        : document.indexing_status || document.display_status || "";
  return {
    label: statuses[status] ?? "待核对",
    type: (["completed", "available"].includes(status)
      ? "success"
      : ["error", "failed"].includes(status)
        ? "danger"
        : ["paused", "stopped"].includes(status)
          ? "warning"
          : "info") as "success" | "danger" | "warning" | "info",
    processing: [
      "waiting",
      "queuing",
      "parsing",
      "cleaning",
      "splitting",
      "indexing",
    ].includes(status),
  };
}
export function citationFromRecord(
  record: Record<string, any>,
): KnowledgeCitation {
  return {
    id: record.citation_id,
    document_name: record.segment?.document?.name ?? record.document_name,
    position: record.segment?.position ?? record.position,
    content: record.segment?.content ?? record.content,
    score: record.score,
  };
}
export function scoreLabel(score: unknown) {
  if (score === null || score === undefined) return "—";
  const number = Number(score);
  return Number.isFinite(number) ? number.toFixed(4) : "—";
}
