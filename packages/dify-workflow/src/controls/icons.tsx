import type { SVGProps } from "react";
export function RiAddLine(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      {...props}
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}
