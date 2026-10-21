/** 来源Dify1.17.1 nodes/variable-assigner/types.ts；原聚合语义与宿主适配见SOURCE_MANIFEST。 */
import type { CommonNodeType, ValueSelector, VarType } from "../../../../types";

export type VarGroupItem = {
  output_type: VarType;
  variables: ValueSelector[];
};
export type VariableAssignerNodeType = CommonNodeType &
  VarGroupItem & {
    advanced_settings: {
      group_enabled: boolean;
      groups: ({
        group_name: string;
        groupId: string;
      } & VarGroupItem)[];
    };
  };
