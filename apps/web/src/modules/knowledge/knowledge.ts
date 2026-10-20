export interface KnowledgeSpace {
  id: string;
  name: string;
  description: string;
  document_count: number;
}
export interface SourceFile {
  id: string;
  name: string;
  content_type: string;
  size: number;
  revision: number;
}
export interface SourceLocation {
  page?: number;
  slide?: number;
  paragraph?: number;
  table?: number;
  row?: number;
  sheet?: string;
}
export interface KnowledgeDocument {
  id: string;
  name: string;
  source: string;
  char_count: number;
  chunk_count: number;
  status: string;
  error?: string;
  error_code?: string;
  created_at: string;
  source_file?: SourceFile;
  parser?: {
    warnings?: { message: string }[];
    phase?: string;
    index_deadline_at?: string;
  };
}
export interface KnowledgeChunk {
  id: string;
  ordinal: number;
  content: string;
  source_location?: SourceLocation;
}
export interface KnowledgeHit extends KnowledgeChunk {
  chunk_id: string;
  document_id: string;
  document_name: string;
  score: number;
  source_file?: SourceFile;
}
export type KnowledgeImport =
  | { kind: "upload"; file: File; name?: string }
  | { kind: "paste"; name: string; text: string }
  | { kind: "file"; file_id: string; name?: string };

export const knowledgeExtensions = [
  ".txt",
  ".md",
  ".csv",
  ".json",
  ".log",
  ".pdf",
  ".docx",
  ".xlsx",
  ".pptx",
];
export const maximumKnowledgeFileSize = 20 * 1024 * 1024;
export function knowledgeFileError(file: { name: string; size: number }) {
  const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!knowledgeExtensions.includes(extension)) return "请选择支持的文档格式";
  if (file.size <= 0) return "文件内容为空";
  if (file.size > maximumKnowledgeFileSize) return "文件不能超过20MB";
  return "";
}
export function sourceLocation(location?: SourceLocation): string {
  if (!location) return "";
  return [
    location.page ? `第${location.page}页` : "",
    location.slide ? `第${location.slide}张幻灯片` : "",
    location.paragraph ? `第${location.paragraph}段` : "",
    location.sheet ? `工作表「${location.sheet}」` : "",
    location.table ? `表${location.table}` : "",
    location.row ? `第${location.row}行` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}
export function documentStatus(document: KnowledgeDocument) {
  if (document.status === "ready")
    return { label: "可检索", type: "success" as const };
  if (document.status === "parsed")
    return { label: "待建索引", type: "info" as const };
  if (document.status === "indexing")
    return { label: "索引中", type: "warning" as const };
  if (document.status === "failed")
    return { label: "处理失败", type: "danger" as const };
  return { label: "待确认", type: "warning" as const };
}
/** 未知或未到期的原索引只允许刷新，是否接替仍由服务端CAS最终裁定。 */
export function canRebuild(document: KnowledgeDocument, now = Date.now()) {
  if (document.chunk_count <= 0) return false;
  if (document.status !== "indexing") return true;
  const deadline = Date.parse(document.parser?.index_deadline_at ?? "");
  return Number.isFinite(deadline) && now > deadline;
}
export function fileSize(bytes: number) {
  return bytes < 1024
    ? `${bytes} B`
    : bytes < 1048576
      ? `${(bytes / 1024).toFixed(1)} KB`
      : `${(bytes / 1048576).toFixed(1)} MB`;
}
