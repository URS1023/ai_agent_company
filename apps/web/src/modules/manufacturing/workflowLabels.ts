import type { Entity } from "../../api/client";

export const workflowLabels: Record<string, string> = {
  open: "待受理",
  contained: "已隔离",
  assigned: "已分派",
  in_progress: "处理中",
  awaiting_reinspection: "待复检",
  awaiting_review: "待复核",
  closed: "已关闭",
  resolved: "已解决",
  hold: "隔离待定",
  rework: "返工",
  repair: "维修",
  scrap: "报废",
  return: "退回",
  concession: "让步接收",
  contain: "隔离",
  assign: "分派",
  start: "开始处理",
  submit: "提交处理",
  submit_reinspection: "提交复检结果",
  verify: "复核通过",
  reject: "退回处理",
  reopen: "重新打开",
  comment: "补充记录",
  created: "创建",
  reinspection_created: "创建复检",
  acknowledged: "已受理",
  acknowledge: "受理",
  normal: "正常",
  attention: "需检查",
  insufficient_data: "数据不足",
};
export function workflowLabel(value: string | undefined) {
  return value ? (workflowLabels[value] ?? value) : "—";
}
export function localTime(value?: string) {
  return value
    ? new Date(value).toLocaleString("zh-CN", { hour12: false })
    : "—";
}

/** 只呈现当前账号在本状态可执行的动作；服务端仍复查身份与版本。 */
export function availableActions(
  row: Entity,
  mode: "quality" | "maintenance",
  userId: string | undefined,
  canWrite: boolean,
  canReview: boolean,
) {
  const actions: string[] = [];
  if (canWrite) {
    if (mode === "quality" && row.status === "open") actions.push("contain");
    if (["open", "contained", "assigned", "in_progress"].includes(row.status))
      actions.push("assign");
    if (row.assignee_id === userId && row.status === "assigned")
      actions.push("start");
    if (row.assignee_id === userId && row.status === "in_progress")
      actions.push(mode === "quality" ? "submit" : "repair");
    if (
      mode === "quality" &&
      row.assignee_id === userId &&
      row.status === "awaiting_reinspection" &&
      row.reinspection?.status === "reviewed" &&
      row.reinspection?.outcome === "passed"
    )
      actions.push("submit_reinspection");
    if (["closed", "resolved"].includes(row.status)) actions.push("reopen");
    actions.push("comment");
  }
  if (
    canReview &&
    row.status === "awaiting_review" &&
    row.performed_by !== userId
  )
    actions.unshift("verify", "reject");
  return actions;
}
