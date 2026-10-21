import { useState } from "react";
import type { InputVar, MoreInfo } from "../types";
const options = [
  ["text-input", "文本"],
  ["paragraph", "段落"],
  ["number", "数字"],
  ["select", "下拉选项"],
  ["checkbox", "勾选"],
] as const;
/** 原config-modal字段结构的本地适配；不读取应用详情或动态文件/插件配置。 */
export default function ConfigVariableModal({
  payload,
  isShow,
  onClose,
  onConfirm,
  varKeys = [],
}: {
  payload?: InputVar;
  isShow: boolean;
  isCreate?: boolean;
  onClose: () => void;
  onConfirm: (value: InputVar, moreInfo?: MoreInfo) => void;
  varKeys?: string[];
}) {
  const [value, setValue] = useState<InputVar>(() =>
    structuredClone(
      payload ?? {
        variable: "",
        label: "",
        type: "text-input",
        required: true,
      },
    ),
  );
  const [error, setError] = useState<Record<string, string>>({});
  if (!isShow) return null;
  const patch = (partial: Partial<InputVar>) =>
    setValue((current) => ({ ...current, ...partial }));
  const submit = () => {
    const errors: Record<string, string> = {};
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,99}$/.test(value.variable))
      errors.variable = "请使用字母或下划线开头的变量名";
    if (
      value.variable !== payload?.variable &&
      varKeys.includes(value.variable)
    )
      errors.variable = "变量名已存在";
    if (!value.label?.trim()) errors.label = "请输入显示名称";
    if (
      value.type === "select" &&
      (!value.options?.length ||
        new Set(value.options).size !== value.options.length ||
        value.options.some((option) => !option.trim()))
    )
      errors.options = "请输入不重复的选项";
    if (
      value.type === "number" &&
      value.default !== undefined &&
      (typeof value.default !== "number" ||
        !Number.isFinite(value.default) ||
        (Number.isInteger(value.default) &&
          !Number.isSafeInteger(value.default)))
    )
      errors.default = "请输入有效数字";
    if (
      value.type === "select" &&
      value.default !== undefined &&
      !(value.options ?? []).includes(String(value.default))
    )
      errors.default = "请选择列表中的默认选项";
    if (
      value.max_length !== undefined &&
      (!Number.isInteger(value.max_length) ||
        value.max_length < 1 ||
        value.max_length > 200000)
    )
      errors.max_length = "请输入1到200000之间的整数";
    setError(errors);
    if (!Object.keys(errors).length)
      onConfirm(
        { ...value, label: value.label.trim() },
        payload?.variable !== value.variable
          ? {
              type: "var-name-change",
              payload: {
                beforeKey: payload?.variable,
                afterKey: value.variable,
              },
            }
          : undefined,
      );
  };
  return (
    <div
      className="dify-modal-backdrop"
      onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={payload ? "编辑输入字段" : "添加输入字段"}
        className="dify-modal"
      >
        <header>
          <strong>{payload ? "编辑输入字段" : "添加输入字段"}</strong>
          <button type="button" aria-label="关闭字段设置" onClick={onClose}>
            ×
          </button>
        </header>
        <label>
          变量名
          <input
            autoFocus
            value={value.variable}
            maxLength={100}
            aria-invalid={!!error.variable}
            onChange={(event) => patch({ variable: event.target.value })}
          />
          {error.variable && <small role="alert">{error.variable}</small>}
        </label>
        <label>
          显示名称
          <input
            value={value.label}
            maxLength={100}
            aria-invalid={!!error.label}
            onChange={(event) => patch({ label: event.target.value })}
          />
          {error.label && <small role="alert">{error.label}</small>}
        </label>
        <label>
          字段类型
          <select
            value={value.type}
            onChange={(event) => {
              const {
                default: _default,
                options: _options,
                json_schema: _schema,
                ...rest
              } = value;
              setValue({
                ...rest,
                type: event.target.value as InputVar["type"],
              });
            }}
          >
            {options.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {value.type === "select" && (
          <label>
            选项
            <textarea
              aria-invalid={!!error.options}
              rows={4}
              value={(value.options ?? []).join("\n")}
              onChange={(event) =>
                patch({ options: event.target.value.split("\n") })
              }
            />
            {error.options && <small role="alert">{error.options}</small>}
          </label>
        )}
        <label className="dify-checkbox">
          <input
            type="checkbox"
            checked={value.required}
            onChange={(event) => patch({ required: event.target.checked })}
          />
          必填
        </label>
        <label>
          默认值
          {value.type === "checkbox" ? (
            <select
              value={value.default === undefined ? "" : String(value.default)}
              onChange={(event) => {
                const next = { ...value };
                if (event.target.value === "") delete next.default;
                else next.default = event.target.value === "true";
                setValue(next);
              }}
            >
              <option value="">未设置</option>
              <option value="true">勾选</option>
              <option value="false">不勾选</option>
            </select>
          ) : value.type === "select" ? (
            <select
              value={String(value.default ?? "")}
              onChange={(event) => {
                const next = { ...value };
                if (event.target.value === "") delete next.default;
                else next.default = event.target.value;
                setValue(next);
              }}
            >
              <option value="">未设置</option>
              {(value.options ?? []).map((option, index) => (
                <option key={index} value={option}>
                  {option}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={value.default === undefined ? "" : String(value.default)}
              type={value.type === "number" ? "number" : "text"}
              onChange={(event) => {
                const next = { ...value };
                if (event.target.value === "") delete next.default;
                else
                  next.default =
                    value.type === "number"
                      ? Number(event.target.value)
                      : event.target.value;
                setValue(next);
              }}
            />
          )}
          {error.default && <small role="alert">{error.default}</small>}
        </label>
        {["text-input", "paragraph"].includes(value.type) && (
          <label>
            最大长度
            <input
              type="number"
              min={1}
              max={200000}
              aria-invalid={!!error.max_length}
              value={value.max_length ?? ""}
              onChange={(event) => {
                const next = { ...value };
                if (event.target.value === "") delete next.max_length;
                else next.max_length = Number(event.target.value);
                setValue(next);
              }}
            />
            {error.max_length && <small role="alert">{error.max_length}</small>}
          </label>
        )}
        <footer>
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button type="button" className="primary" onClick={submit}>
            确定
          </button>
        </footer>
      </section>
    </div>
  );
}
