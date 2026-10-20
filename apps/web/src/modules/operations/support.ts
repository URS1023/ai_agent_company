/** 系统辅助配置契约；作用域、版本及表单判定由服务端确认。 */
export type SupportKind = "dictionary" | "parameter" | "announcement" | "form";
export interface Choice {
  label: string;
  value: string;
  enabled?: boolean;
  order?: number;
  is_default?: boolean;
  tag_type?: "info" | "success" | "warning" | "danger" | "primary";
}
export interface FormField {
  key: string;
  label: string;
  kind:
    "text" | "textarea" | "number" | "integer" | "select" | "boolean" | "date";
  required: boolean;
  placeholder?: string;
  minimum?: number;
  maximum?: number;
  max_length?: number;
  choices?: Choice[];
}
export interface SupportResource {
  id: string;
  kind: SupportKind;
  code: string;
  name: string;
  scope: "workspace" | "personal";
  enabled: boolean;
  revision: number;
  definition: Record<string, any>;
  updated_at: string;
}
export const sections: { kind: SupportKind; label: string; plural: string }[] =
  [
    { kind: "dictionary", label: "字典", plural: "dictionaries" },
    { kind: "parameter", label: "参数", plural: "parameters" },
    { kind: "announcement", label: "公告", plural: "announcements" },
    { kind: "form", label: "表单", plural: "forms" },
  ];
export function resourcePath(kind: SupportKind, id?: string) {
  return `/operations/${sections.find((item) => item.kind === kind)!.plural}${id ? `/${encodeURIComponent(id)}` : ""}`;
}
export function dateLabel(value?: string) {
  return value
    ? new Date(value).toLocaleString("zh-CN", { hour12: false })
    : "—";
}
export function download(
  content: string | Blob,
  name: string,
  type = "application/json",
) {
  const url = URL.createObjectURL(
    content instanceof Blob ? content : new Blob([content], { type }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
