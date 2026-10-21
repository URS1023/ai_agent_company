import type { ReactNode } from "react";
/** 保留原Field的标题、操作与字段内容三分区，去除依赖原应用的提示服务。 */
export default function Field({
  title,
  operations,
  children,
  className = "",
  required,
}: {
  title: ReactNode;
  operations?: ReactNode;
  children?: ReactNode;
  className?: string;
  required?: boolean;
}) {
  return (
    <section className={`dify-field ${className}`}>
      <div className="dify-field-heading">
        <strong>
          {title}
          {required && <span aria-hidden="true"> *</span>}
        </strong>
        <span>{operations}</span>
      </div>
      {children}
    </section>
  );
}
