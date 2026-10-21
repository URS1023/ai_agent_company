/** 来源Dify 1.17.1 nodes/end/types.ts；平台隔离适配见SOURCE_MANIFEST.json。 */
import type { CommonNodeType, Variable } from "../../../../types";

export type EndNodeType = CommonNodeType & {
  outputs: Variable[];
};
