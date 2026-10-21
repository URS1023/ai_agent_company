import type { CaseItem, Condition } from "../types";
import { comparisonOperatorNotRequireValue, getOperators } from "../utils";
import VariablePicker from "../../../../../controls/VariablePicker";
import type { VariableOption } from "../../../../../types";
const operatorNames: Record<string, string> = {
  contains: "包含",
  "not contains": "不包含",
  "start with": "开头是",
  "end with": "结尾是",
  is: "是",
  "is not": "不是",
  empty: "为空",
  "not empty": "不为空",
  "is null": "为null",
  "is not null": "非null",
};
interface Props {
  nodeId: string;
  cases: CaseItem[];
  readOnly: boolean;
  handleSortCase?: (cases: (CaseItem & { id: string })[]) => void;
  handleRemoveCase?: (id: string) => void;
  handleAddCondition?: (
    caseId: string,
    selector: string[],
    option: VariableOption,
  ) => void;
  handleRemoveCondition?: (caseId: string, id: string) => void;
  handleUpdateCondition?: (
    caseId: string,
    id: string,
    condition: Condition,
  ) => void;
  handleToggleConditionLogicalOperator?: (caseId: string) => void;
  [key: string]: unknown;
}
/** 精提原condition-wrap/condition-list的IF/ELIF、AND/OR分组交互；平台仅提供本地变量，不访问原工具目录。 */
export default function ConditionWrap(props: Props) {
  const { cases, nodeId, readOnly } = props;
  const move = (index: number, delta: number) => {
    const next = cases.map((item) => ({ ...item, id: item.case_id }));
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target]!, next[index]!];
    props.handleSortCase?.(next);
  };
  return (
    <div className="dify-cases">
      {cases.map((item, index) => (
        <section key={item.case_id} className="dify-case">
          <header>
            <strong>{index === 0 ? "IF" : "ELIF"}</strong>
            {cases.length > 1 && <span>CASE {index + 1}</span>}
            <span className="grow" />
            {!readOnly && cases.length > 1 && (
              <>
                <button
                  type="button"
                  disabled={index === 0}
                  aria-label="上移分支"
                  onClick={() => move(index, -1)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  disabled={index === cases.length - 1}
                  aria-label="下移分支"
                  onClick={() => move(index, 1)}
                >
                  ↓
                </button>
                <button
                  type="button"
                  aria-label="删除分支"
                  onClick={() => props.handleRemoveCase?.(item.case_id)}
                >
                  ×
                </button>
              </>
            )}
          </header>
          <div className="dify-conditions">
            {item.conditions.length > 1 && (
              <button
                className="dify-logical"
                disabled={readOnly}
                type="button"
                onClick={() =>
                  props.handleToggleConditionLogicalOperator?.(item.case_id)
                }
              >
                {item.logical_operator.toUpperCase()}
              </button>
            )}
            <div className="grow">
              {item.conditions.map((condition) => {
                const patch = (value: Partial<Condition>) =>
                  props.handleUpdateCondition?.(item.case_id, condition.id, {
                    ...condition,
                    ...value,
                  });
                return (
                  <div key={condition.id} className="dify-condition">
                    <VariablePicker
                      nodeId={nodeId}
                      disabled={readOnly}
                      value={condition.variable_selector ?? []}
                      onChange={(selector, option) =>
                        patch({
                          variable_selector: selector,
                          varType: option.type,
                          comparison_operator: getOperators(option.type)[0],
                          value: option.type === "boolean" ? false : "",
                        })
                      }
                    />
                    <div className="dify-condition-value">
                      <select
                        aria-label="比较方式"
                        value={condition.comparison_operator ?? ""}
                        disabled={readOnly}
                        onChange={(event) =>
                          patch({
                            comparison_operator: event.target
                              .value as Condition["comparison_operator"],
                          })
                        }
                      >
                        {getOperators(condition.varType).map((operator) => (
                          <option key={operator} value={operator}>
                            {operatorNames[operator] ?? operator}
                          </option>
                        ))}
                      </select>
                      {!comparisonOperatorNotRequireValue(
                        condition.comparison_operator,
                      ) &&
                        (condition.varType === "boolean" ? (
                          <select
                            aria-label="比较值"
                            disabled={readOnly}
                            value={String(condition.value)}
                            onChange={(event) =>
                              patch({ value: event.target.value === "true" })
                            }
                          >
                            <option value="false">否</option>
                            <option value="true">是</option>
                          </select>
                        ) : (
                          <input
                            aria-label="比较值"
                            value={
                              typeof condition.value === "string" ||
                              typeof condition.value === "number"
                                ? condition.value
                                : ""
                            }
                            disabled={readOnly}
                            inputMode={
                              condition.varType === "number"
                                ? "decimal"
                                : "text"
                            }
                            onChange={(event) =>
                              patch({ value: event.target.value })
                            }
                          />
                        ))}
                      {!readOnly && (
                        <button
                          type="button"
                          aria-label="删除条件"
                          onClick={() =>
                            props.handleRemoveCondition?.(
                              item.case_id,
                              condition.id,
                            )
                          }
                        >
                          ×
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          {!readOnly && (
            <div className="dify-add-condition">
              <label>
                添加条件
                <VariablePicker
                  nodeId={nodeId}
                  value={[]}
                  onChange={(selector, option) =>
                    props.handleAddCondition?.(item.case_id, selector, option)
                  }
                />
              </label>
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
