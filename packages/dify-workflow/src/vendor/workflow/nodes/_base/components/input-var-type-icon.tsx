import type { InputVarType } from "../../../../../types";
const symbols: Record<string, string> = {
  "text-input": "T",
  paragraph: "≡",
  select: "▤",
  number: "#",
  checkbox: "✓",
  json_object: "{}",
};
export default function InputVarTypeIcon({
  type,
  className,
}: {
  type: InputVarType;
  className?: string;
}) {
  return (
    <span className={className} aria-hidden="true">
      {symbols[type] ?? "T"}
    </span>
  );
}
