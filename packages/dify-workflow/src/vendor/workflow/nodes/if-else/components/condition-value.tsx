import type { ComparisonOperator } from "../types";
import { comparisonOperatorNotRequireValue } from "../utils";
import { VariableLabelInText } from "../../../../../controls/VariableLabel";
/** 沿用原条件值顺序，0/false直接呈现，不使用truthy判断丢值。 */
export default function ConditionValue({
  variableSelector,
  operator,
  value,
}: {
  variableSelector: string[];
  operator: ComparisonOperator;
  value: unknown;
}) {
  return (
    <div className="dify-condition-summary">
      <VariableLabelInText variables={variableSelector} />
      <span>{operator}</span>
      {!comparisonOperatorNotRequireValue(operator) && (
        <span>
          {typeof value === "boolean"
            ? value
              ? "是"
              : "否"
            : Array.isArray(value)
              ? value.join(", ")
              : String(value ?? "")}
        </span>
      )}
    </div>
  );
}
