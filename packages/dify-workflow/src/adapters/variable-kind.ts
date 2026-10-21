export const VarKindType = {
  variable: "variable",
  constant: "constant",
  mixed: "mixed",
} as const;
export type VarKindType = (typeof VarKindType)[keyof typeof VarKindType];
