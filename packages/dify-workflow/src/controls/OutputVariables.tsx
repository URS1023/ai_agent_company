import type { Variable } from "../types";
import VariablePicker from "./VariablePicker";
/** 原End输出列表的名称与引用分栏；引用只来源于当前连通上游。 */
export default function OutputVariables({
  nodeId,
  readonly,
  list,
  onChange,
}: {
  nodeId: string;
  readonly: boolean;
  list: Variable[];
  onChange: (values: Variable[]) => void;
}) {
  const patch = (index: number, value: Partial<Variable>) =>
    onChange(
      list.map((item, position) =>
        position === index ? { ...item, ...value } : item,
      ),
    );
  return (
    <div className="dify-output-list">
      {list.map((item, index) => (
        <div className="dify-output-row" key={index}>
          <input
            aria-label={`输出名称${index + 1}`}
            value={item.variable}
            disabled={readonly}
            maxLength={100}
            onChange={(event) => patch(index, { variable: event.target.value })}
          />
          <VariablePicker
            nodeId={nodeId}
            value={item.value_selector}
            disabled={readonly}
            onChange={(selector, option) =>
              patch(index, {
                value_selector: selector,
                value_type: option.type,
              })
            }
          />
          {!readonly && (
            <button
              aria-label={`删除输出${index + 1}`}
              type="button"
              onClick={() =>
                onChange(list.filter((_, position) => position !== index))
              }
            >
              ×
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
