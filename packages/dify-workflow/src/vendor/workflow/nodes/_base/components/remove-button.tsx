/** 来源Dify1.17.1 nodes/_base/components/remove-button.tsx；原聚合语义与宿主适配见SOURCE_MANIFEST。 */
"use client";
import type { FC } from "react";
import { IconButton } from "../../../../../controls/primitives";
import * as React from "react";
import { useTranslation } from "../../../../../adapters/i18n";

type Props = Readonly<{
  className?: string;
  onClick: (e: React.MouseEvent) => void;
}>;

const Remove: FC<Props> = ({ onClick }) => {
  const { t } = useTranslation(["common"]);
  return (
    <IconButton
      aria-label={t(($) => $["operation.remove"], { ns: "common" })}
      size="lg"
      tone="destructive"
      className="shrink-0"
      onClick={onClick}
    >
      <span aria-hidden="true" className="i-ri-delete-bin-line size-4" />
    </IconButton>
  );
};
export default React.memo(Remove);
