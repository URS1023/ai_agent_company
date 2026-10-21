import { useState } from "react";
import { useEditor } from "../EditorContext";
import { availableVariables } from "../draft";
import type { VariableOption } from "../types";
export default function VariablePicker({
  nodeId,
  value,
  onChange,
  disabled,
  label = "选择变量",
  options: suppliedOptions,
}: {
  nodeId: string;
  value: string[];
  onChange: (selector: string[], option: VariableOption) => void;
  disabled?: boolean;
  label?: string;
  options?: VariableOption[];
}) {
  const { definition } = useEditor(),
    [search, setSearch] = useState("");
  const options = suppliedOptions ?? availableVariables(definition, nodeId);
  return (
    <div className="dify-variable-picker">
      <input
        aria-label="搜索变量"
        placeholder="搜索变量"
        value={search}
        disabled={disabled}
        onChange={(event) => setSearch(event.target.value)}
      />
      <select
        aria-label={label}
        disabled={disabled}
        value={JSON.stringify(value)}
        onChange={(event) => {
          const option = options.find(
            (item) => JSON.stringify(item.selector) === event.target.value,
          );
          if (option) onChange([...option.selector], option);
        }}
      >
        <option value={JSON.stringify([])}>选择变量</option>
        {value.length > 0 &&
          !options.some(
            (option) =>
              JSON.stringify(option.selector) === JSON.stringify(value),
          ) && (
            <option value={JSON.stringify(value)} disabled>
              引用不可用：{value.slice(1).join(".")}
            </option>
          )}
        {options
          .filter(
            (option) =>
              JSON.stringify(option.selector) === JSON.stringify(value) ||
              option.label.toLowerCase().includes(search.toLowerCase()),
          )
          .map((option) => (
            <option
              key={JSON.stringify(option.selector)}
              value={JSON.stringify(option.selector)}
            >
              {option.label} · {option.type}
            </option>
          ))}
      </select>
    </div>
  );
}
