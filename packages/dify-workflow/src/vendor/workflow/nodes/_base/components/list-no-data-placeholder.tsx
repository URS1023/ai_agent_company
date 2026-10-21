/** 来源Dify1.17.1 nodes/_base/components/list-no-data-placeholder.tsx；原聚合语义与宿主适配见SOURCE_MANIFEST。 */
"use client";
import type { FC } from "react";
import * as React from "react";

type Props = Readonly<{
  children: React.ReactNode;
}>;

const ListNoDataPlaceholder: FC<Props> = ({ children }) => {
  return (
    <div className="flex min-h-10.5 w-full items-center justify-center rounded-[10px] bg-background-section system-xs-regular text-text-tertiary">
      {children}
    </div>
  );
};
export default React.memo(ListNoDataPlaceholder);
