/** 来源Dify1.17.1 nodes/_base/components/add-button.tsx；原聚合语义与宿主适配见SOURCE_MANIFEST。 */
"use client";
import type { FC } from "react";
import { Button } from "../../../../../controls/primitives";
import { cn } from "../../../../../controls/primitives";
import { RiAddLine } from "../../../../../controls/icons";
import * as React from "react";

type Props = Readonly<{
  className?: string;
  text: string;
  onClick: () => void;
}>;

const AddButton: FC<Props> = ({ className, text, onClick }) => {
  return (
    <Button
      className={cn("w-full", className)}
      variant="tertiary"
      size="medium"
      onClick={onClick}
    >
      <RiAddLine className="size-3.5" />
      <div>{text}</div>
    </Button>
  );
};
export default React.memo(AddButton);
