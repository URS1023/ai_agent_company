import type { ButtonHTMLAttributes } from "react";
export const cn = (...values: unknown[]) => values.filter(Boolean).join(" ");
export function IconButton({
  tone: _tone,
  size: _size,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: string; size?: string }) {
  return <button type="button" {...props} />;
}
export function Switch({
  checked,
  disabled,
  onCheckedChange,
}: {
  checked?: boolean;
  disabled?: boolean;
  size?: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <input
      type="checkbox"
      aria-label="分组聚合"
      checked={!!checked}
      disabled={disabled}
      onChange={(event) => onCheckedChange(event.target.checked)}
    />
  );
}
export function Button({
  variant: _variant,
  size: _size,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: string;
  size?: string;
}) {
  return <button type="button" {...props} />;
}
