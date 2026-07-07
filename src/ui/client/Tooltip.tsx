import React, { useRef, useState } from "react";
import { createPortal } from "react-dom";

export default function Tooltip({
  label,
  children,
  placement = "top",
}: {
  label: string;
  children: React.ReactNode;
  placement?: "top" | "bottom";
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);

  const show = () => setRect(ref.current?.getBoundingClientRect() ?? null);
  const hide = () => setRect(null);

  const style: React.CSSProperties = rect
    ? {
        position: "fixed",
        left: rect.left + rect.width / 2,
        top: placement === "top" ? rect.top - 8 : rect.bottom + 8,
        transform: placement === "top" ? "translate(-50%, -100%)" : "translate(-50%, 0)",
      }
    : {};

  return (
    <span
      ref={ref}
      className="tt-wrap"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {rect && label &&
        createPortal(
          <span className={`tooltip tooltip-${placement}`} style={style} role="tooltip">
            {label}
          </span>,
          document.body,
        )}
    </span>
  );
}
