import type { ReactNode } from "react";
/** 原输出分区的局部适配，不展示重复规则或调试器服务信息。 */
export default function OutputVars({ children }: { children: ReactNode }) {
  return (
    <section className="dify-output-summary">
      <strong>输出变量</strong>
      {children}
    </section>
  );
}
export function VarItem({
  name,
  type,
}: {
  name: string;
  type: string;
  description?: string;
}) {
  return (
    <div className="dify-variable-label">
      <code>{name}</code>
      <span>{type}</span>
    </div>
  );
}
