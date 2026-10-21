/** 来源Dify 1.17.1 nodes/start/types.ts；平台隔离适配见SOURCE_MANIFEST.json。 */
import type { CommonNodeType, InputVar } from "../../../../types";

export type StartNodeType = CommonNodeType & {
  variables: InputVar[];
};
