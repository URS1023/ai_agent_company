import { useState } from "react";
import type { InputVar, MoreInfo } from "../../../../../types";
import ConfigVariableModal from "../../../../../controls/ConfigVariableModal";
import InputVarTypeIcon from "../../_base/components/input-var-type-icon";
/** 沿用原start/components/var-list与var-item的变量/标签/必填/类型顺序，编辑按钮始终可聚焦。 */
export default function VarList({
  readonly,
  list,
  onChange,
}: {
  readonly: boolean;
  list: InputVar[];
  onChange: (
    list: InputVar[],
    moreInfo?: { index: number; payload: MoreInfo },
  ) => void;
}) {
  const [editing, setEditing] = useState<number | null>(null);
  return (
    <div className="dify-start-fields">
      {list.map((item, index) => (
        <div className="dify-start-field" key={item.variable}>
          <span className="dify-variable-mark">{item.variable}</span>
          <span className="dify-field-label">{item.label}</span>
          {item.required && <small>必填</small>}
          <InputVarTypeIcon type={item.type} />
          {!readonly && item.type !== "json_object" && (
            <>
              <button
                type="button"
                aria-label={`编辑${item.label || item.variable}`}
                onClick={() => setEditing(index)}
              >
                编辑
              </button>
              <button
                type="button"
                aria-label={`删除${item.label || item.variable}`}
                onClick={() =>
                  onChange(list.filter((_, position) => position !== index))
                }
              >
                ×
              </button>
            </>
          )}
        </div>
      ))}
      {editing !== null && list[editing] && (
        <ConfigVariableModal
          key={editing}
          isShow
          payload={list[editing]}
          varKeys={list.map((item) => item.variable)}
          onClose={() => setEditing(null)}
          onConfirm={(next, moreInfo) => {
            onChange(
              list.map((item, index) => (index === editing ? next : item)),
              moreInfo ? { index: editing, payload: moreInfo } : undefined,
            );
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}
