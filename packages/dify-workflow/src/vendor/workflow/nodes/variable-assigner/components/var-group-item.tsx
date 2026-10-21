/** 来源Dify1.17.1 nodes/variable-assigner/components/var-group-item.tsx；原聚合语义与宿主适配见SOURCE_MANIFEST。 */
"use client";
import type { ChangeEvent, FC } from "react";
import type { VarGroupItem as VarGroupItemType } from "../types";
import type { NodeOutPutVar, ValueSelector, Var } from "../../../../../types";
import { RiDeleteBinLine } from "../../../../../controls/icons";
import { produce } from "immer";
import * as React from "react";
import { useCallback, useState } from "react";
import { useTranslation } from "../../../../../adapters/i18n";
import Field from "../../../../../controls/Field";
import { VarKindType } from "../../../../../adapters/variable-kind";
import { VarType } from "../../../../../types";
import { useEditor } from "../../../../../EditorContext";
import {
  checkKeys,
  replaceSpaceWithUnderscoreInVarNameInput,
} from "../../../../../adapters/variableKeys";
import VarReferencePicker from "../../../../../controls/VarReferencePicker";
import VarList from "../components/var-list";

const i18nPrefix = "nodes.variableAssigner";

type Payload = VarGroupItemType & {
  group_name?: string;
};

type Props = Readonly<{
  readOnly: boolean;
  nodeId: string;
  payload: Payload;
  onChange: (newPayload: Payload) => void;
  groupEnabled: boolean;
  onGroupNameChange?: (value: string) => void;
  canRemove?: boolean;
  onRemove?: () => void;
  availableVars: NodeOutPutVar[];
}>;

const VarGroupItem: FC<Props> = ({
  readOnly,
  nodeId,
  payload,
  onChange,
  groupEnabled,
  onGroupNameChange,
  canRemove,
  onRemove,
  availableVars,
}) => {
  const { t } = useTranslation(["appDebug", "workflow"]);
  const { reportError } = useEditor();

  const handleAddVariable = useCallback(
    (
      value: ValueSelector | string,
      _varKindType: VarKindType,
      varInfo?: Var,
    ) => {
      const chosenVariables = payload.variables;
      if (
        chosenVariables.some(
          (item) => item.join(".") === (value as ValueSelector).join("."),
        )
      )
        return;

      const newPayload = produce(payload, (draft: Payload) => {
        draft.variables.push(value as ValueSelector);
        if (varInfo && varInfo.type !== VarType.any)
          draft.output_type = varInfo.type;
      });
      onChange(newPayload);
    },
    [onChange, payload],
  );

  const handleListChange = useCallback(
    (newList: ValueSelector[], changedItem?: ValueSelector) => {
      if (changedItem) {
        const chosenVariables = payload.variables;
        if (
          chosenVariables.some(
            (item) => item.join(".") === changedItem.join("."),
          )
        )
          return;
      }

      const newPayload = produce(payload, (draft) => {
        draft.variables = newList;
        if (newList.length === 0) draft.output_type = VarType.any;
      });
      onChange(newPayload);
    },
    [onChange, payload],
  );

  const filterVar = useCallback(
    (varPayload: Var) => {
      if (payload.output_type === VarType.any) return true;
      return varPayload.type === payload.output_type;
    },
    [payload.output_type],
  );

  const [isEditGroupName, setIsEditGroupName] = useState(false);

  const handleGroupNameChange = useCallback(
    (e: ChangeEvent<any>) => {
      replaceSpaceWithUnderscoreInVarNameInput(e.target);
      const value = e.target.value;
      const { isValid, errorKey, errorMessageKey } = checkKeys([value], false);
      if (!isValid) {
        reportError(
          t(($) => $[`varKeyError.${errorMessageKey}`], {
            ns: "appDebug",
            key: errorKey,
          }),
        );
        return;
      }
      onGroupNameChange?.(value);
    },
    [onGroupNameChange, t],
  );

  return (
    <Field
      className="group"
      title={
        groupEnabled ? (
          <div className="flex items-center">
            <div className="flex items-center normal-case!">
              <span
                aria-hidden
                className="mr-0.5 i-custom-vender-line-files-folder size-3.5"
              />
              {!isEditGroupName ? (
                <button
                  type="button"
                  disabled={readOnly}
                  aria-label={`编辑分组${payload.group_name}`}
                  className="flex h-6 cursor-text items-center rounded-lg px-1 system-sm-semibold text-text-secondary hover:bg-gray-100"
                  onClick={() => {
                    if (!readOnly) setIsEditGroupName(true);
                  }}
                >
                  {payload.group_name}
                </button>
              ) : (
                <input
                  type="text"
                  aria-label="分组名称"
                  disabled={readOnly}
                  className="h-6 rounded-lg border border-gray-300 bg-white px-1 focus:outline-hidden"
                  size={payload.group_name?.length}
                  autoFocus
                  value={payload.group_name}
                  onChange={handleGroupNameChange}
                  onBlur={() => setIsEditGroupName(false)}
                  maxLength={30}
                />
              )}
            </div>
            {canRemove && (
              <button
                type="button"
                aria-label={`删除分组${payload.group_name}`}
                className="ml-0.5 cursor-pointer rounded-md p-1 text-text-tertiary group-hover:block hover:bg-state-destructive-hover hover:text-text-destructive"
                onClick={onRemove}
              >
                <RiDeleteBinLine className="size-4" />
              </button>
            )}
          </div>
        ) : (
          t(($) => $[`${i18nPrefix}.title`], { ns: "workflow" })!
        )
      }
      operations={
        <div className="flex h-6 items-center space-x-2">
          {payload.variables.length > 0 && (
            <div className="flex h-4.5 items-center rounded-[5px] border border-divider-deep px-1 system-2xs-medium-uppercase text-text-tertiary">
              {payload.output_type}
            </div>
          )}
          {!readOnly ? (
            <VarReferencePicker
              isAddBtnTrigger
              readonly={false}
              nodeId={nodeId}
              isShowNodeName
              value={[]}
              onChange={handleAddVariable}
              defaultVarKindType={VarKindType.variable}
              filterVar={filterVar}
              availableVars={availableVars}
            />
          ) : undefined}
        </div>
      }
    >
      <VarList
        readonly={readOnly}
        nodeId={nodeId}
        list={payload.variables}
        onChange={handleListChange}
        filterVar={filterVar}
        availableVars={availableVars}
      />
    </Field>
  );
};
export default React.memo(VarGroupItem);
