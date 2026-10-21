import type { ValueSelector, Var, VariableOption } from "../types";
import { availableVariables } from "../variables";
import { useEditor } from "../EditorContext";
import VariablePicker from "./VariablePicker";

/** 原引用控件的同源适配；候选只来自当前图，不读取原console或工具目录。 */
export default function VarReferencePicker(props: {
  nodeId: string;
  value: string[];
  readonly?: boolean;
  availableVars?: VariableOption[];
  filterVar?: (payload: Var, selector: ValueSelector) => boolean;
  onChange: (value: ValueSelector, kind: "variable", info?: Var) => void;
  isAddBtnTrigger?: boolean;
  [key: string]: unknown;
}) {
  const { definition } = useEditor();
  const options = (
    props.availableVars ?? availableVariables(definition, props.nodeId)
  ).filter(
    (option) =>
      !props.filterVar ||
      props.filterVar(
        { variable: option.selector.slice(1).join("."), type: option.type },
        option.selector,
      ),
  );
  return (
    <VariablePicker
      nodeId={props.nodeId}
      value={props.value}
      options={options}
      disabled={props.readonly}
      label={props.isAddBtnTrigger ? "添加聚合变量" : "聚合变量"}
      onChange={(selector, option) =>
        props.onChange(selector, "variable", {
          variable: selector.slice(1).join("."),
          type: option.type,
        })
      }
    />
  );
}
