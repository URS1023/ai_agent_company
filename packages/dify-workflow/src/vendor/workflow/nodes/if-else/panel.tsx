/** 来源Dify 1.17.1 nodes/if-else/panel.tsx；平台隔离适配见SOURCE_MANIFEST.json。 */
import type { FC } from "react";
import type { IfElseNodeType } from "./types";
import type { NodePanelProps } from "../../../../types";
import { Button } from "../../../../controls/primitives";
import { RiAddLine } from "../../../../controls/icons";
import { memo } from "react";
import { useTranslation } from "../../../../adapters/i18n";
import Field from "../../../../controls/Field";
import ConditionWrap from "./components/condition-wrap";
import useConfig from "./use-config";

const i18nPrefix = "nodes.ifElse";

const Panel: FC<NodePanelProps<IfElseNodeType>> = ({ id, data }) => {
  const { t } = useTranslation(["workflowLogic"]);
  const {
    readOnly,
    inputs,
    filterVar,
    handleAddCase,
    handleRemoveCase,
    handleSortCase,
    handleAddCondition,
    handleUpdateCondition,
    handleRemoveCondition,
    handleToggleConditionLogicalOperator,
    handleAddSubVariableCondition,
    handleRemoveSubVariableCondition,
    handleUpdateSubVariableCondition,
    handleToggleSubVariableConditionLogicalOperator,
    nodesOutputVars,
    availableNodes,
    varsIsVarFileAttribute,
  } = useConfig(id, data);
  const cases = inputs.cases || [];

  return (
    <div className="p-1">
      <ConditionWrap
        nodeId={id}
        cases={cases}
        readOnly={readOnly}
        handleSortCase={handleSortCase}
        handleRemoveCase={handleRemoveCase}
        handleAddCondition={handleAddCondition}
        handleRemoveCondition={handleRemoveCondition}
        handleUpdateCondition={handleUpdateCondition}
        handleToggleConditionLogicalOperator={
          handleToggleConditionLogicalOperator
        }
        handleAddSubVariableCondition={handleAddSubVariableCondition}
        handleRemoveSubVariableCondition={handleRemoveSubVariableCondition}
        handleUpdateSubVariableCondition={handleUpdateSubVariableCondition}
        handleToggleSubVariableConditionLogicalOperator={
          handleToggleSubVariableConditionLogicalOperator
        }
        nodesOutputVars={nodesOutputVars}
        availableNodes={availableNodes}
        varsIsVarFileAttribute={varsIsVarFileAttribute}
        filterVar={filterVar}
      />
      <div className="px-4 py-2">
        <Button
          className="w-full"
          variant="tertiary"
          onClick={() => handleAddCase()}
          disabled={readOnly}
        >
          <RiAddLine className="size-4" />
          ELIF
        </Button>
      </div>
      <div className="mx-3 my-2 h-px bg-divider-subtle"></div>
      <Field
        title={t(($) => $[`${i18nPrefix}.else`], { ns: "workflowLogic" })}
        className="px-4 py-2"
      ></Field>
    </div>
  );
};

export default memo(Panel);
