import type { ButtonHTMLAttributes } from "react";
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
