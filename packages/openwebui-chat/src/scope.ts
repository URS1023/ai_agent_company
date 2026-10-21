import { writable } from "svelte/store";
/** 每个挂载实例独有，不复制原应用的全局账号、Socket或持久化store。 */
export function createChatScope() {
  const dictionary: Record<string, string> = {
    Thoughts: "思考过程",
    "Thinking...": "思考中…",
    "Show more": "展开",
    "Show less": "收起",
    "Tool Calls": "工具调用",
    "Tool call": "工具调用",
    Approve: "允许",
    Reject: "拒绝",
    Completed: "已完成",
    Running: "执行中",
    Error: "错误",
    "Thought for less than a second": "思考不足1秒",
    "Thought for {{DURATION}} seconds": "思考{{DURATION}}秒",
    "Thought for {{DURATION}}": "思考{{DURATION}}",
    "Processing...": "处理中…",
    Thought: "思考过程",
    Analyzed: "已分析",
    "Analyzing...": "分析中…",
    Allow: "允许",
    Deny: "拒绝",
    tool: "工具",
    Exploring: "正在分析",
    Explored: "已分析",
    "Toggle details": "展开或收起详情",
    "Ran {{COUNT}} analysis": "执行{{COUNT}}次分析",
    "Ran {{COUNT}} analyses": "执行{{COUNT}}次分析",
  };
  return {
    settings: writable({ expandDetails: false, terminalFileDisplay: "none" }),
    i18n: writable({
      language: "zh-CN",
      t: (key: string, values: Record<string, unknown> = {}) => {
        let value = dictionary[key] ?? key;
        for (const [name, content] of Object.entries(values))
          value = value.replaceAll(`{{${name}}}`, String(content));
        return value;
      },
    }),
  };
}
