/** 来源Dify 1.17.1 nodes/start/panel.tsx；平台隔离适配见SOURCE_MANIFEST.json。 */
import type { FC } from "react";
import type { StartNodeType } from "./types";
import type { InputVar, NodePanelProps } from "../../../../types";
import * as React from "react";
import { useTranslation } from "../../../../adapters/i18n";
import ConfigVarModal from "../../../../controls/ConfigVariableModal";
import Field from "../../../../controls/Field";
import RemoveEffectVarConfirm from "../_base/components/remove-effect-var-confirm";
import VarList from "./components/var-list";
import useConfig from "./use-config";

const i18nPrefix = "nodes.start";

const Panel: FC<NodePanelProps<StartNodeType>> = ({ id, data }) => {
  const { t } = useTranslation(["common", "workflow"]);
  const {
    readOnly,
    inputs,
    isShowAddVarModal,
    showAddVarModal,
    handleAddVariable,
    hideAddVarModal,
    handleVarListChange,
    isShowRemoveVarConfirm,
    hideRemoveVarConfirm,
    onRemoveVarConfirm,
  } = useConfig(id, data);

  const handleAddVarConfirm = (payload: InputVar) => {
    const isValid = handleAddVariable(payload);
    if (!isValid) return;
    hideAddVarModal();
  };

  return (
    <div className="mt-2">
      <div className="space-y-4 px-4 pb-2">
        <Field
          title={t(($) => $[`${i18nPrefix}.inputField`], { ns: "workflow" })}
          operations={
            !readOnly ? (
              <button
                type="button"
                aria-label={`${t(($) => $["operation.add"], { ns: "common" })} ${t(($) => $[`${i18nPrefix}.inputField`], { ns: "workflow" })}`}
                className="cursor-pointer rounded-md border-none bg-transparent p-1 select-none hover:bg-state-base-hover focus-visible:ring-1 focus-visible:ring-components-input-border-active focus-visible:outline-hidden"
                onClick={showAddVarModal}
              >
                <span
                  className="i-ri-add-line size-4 text-text-tertiary"
                  aria-hidden="true"
                />
              </button>
            ) : undefined
          }
        >
          <>
            <VarList
              readonly={readOnly}
              list={inputs.variables || []}
              onChange={handleVarListChange}
            />
          </>
        </Field>
      </div>

      {isShowAddVarModal && (
        <ConfigVarModal
          isCreate
          isShow={isShowAddVarModal}
          onClose={hideAddVarModal}
          onConfirm={handleAddVarConfirm}
          varKeys={inputs.variables.map((v) => v.variable)}
        />
      )}

      <RemoveEffectVarConfirm
        isShow={isShowRemoveVarConfirm}
        onCancel={hideRemoveVarConfirm}
        onConfirm={onRemoveVarConfirm}
      />
    </div>
  );
};

export default React.memo(Panel);
