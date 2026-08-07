import React, { useEffect, useRef, useState } from "react";
import { Copy, Check } from "lucide-react";

async function copyText(value: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
  } catch {
    // WebKit and embedded browsers can deny Clipboard API despite localhost.
  }

  const field = document.createElement("textarea");
  field.value = value;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  field.style.pointerEvents = "none";
  const activeElement = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
  document.body.appendChild(field);
  let copied = false;
  try {
    field.select();
    copied = document.execCommand("copy");
  } finally {
    field.remove();
    activeElement?.focus({ preventScroll: true });
  }
  if (!copied) throw new Error("Clipboard unavailable");
}

type CopyButtonProps = {
  text?: string;
  getText?: () => string;
  label?: string;
  copiedLabel?: string;
  className?: string;
  title?: string;
  disabled?: boolean;
  stopPropagation?: boolean;
  iconOnly?: boolean;
  resetMs?: number;
  style?: React.CSSProperties;
  onCopied?: () => void;
};

export default function CopyButton({
  text,
  getText,
  label = "Copy",
  copiedLabel = "Copied",
  className = "copy-btn",
  title,
  disabled = false,
  stopPropagation = false,
  iconOnly = false,
  resetMs = 1600,
  style,
  onCopied,
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, []);

  const handleCopy = async (event: React.MouseEvent<HTMLButtonElement>) => {
    if (stopPropagation) event.stopPropagation();
    const value = getText ? getText() : text;
    if (!value) return;

    try {
      await copyText(value);
      setCopied(true);
      onCopied?.();

      if (timerRef.current) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => setCopied(false), resetMs);
    } catch {
      setCopied(false);
    }
  };

  return (
    <button
      type="button"
      className={`${className} copy-button ${copied ? "is-copied" : ""} ${iconOnly ? "is-icon-only" : ""}`}
      onClick={handleCopy}
      disabled={disabled}
      title={title ?? label}
      aria-label={copied ? copiedLabel : label}
      aria-live="polite"
      style={style}
    >
      <span className="copy-button-layer copy-button-idle">
        <Copy className="copy-button-icon" aria-hidden="true" />
        {!iconOnly && <span>{label}</span>}
      </span>
      <span className="copy-button-layer copy-button-success">
        <Check className="copy-button-icon" aria-hidden="true" />
        {!iconOnly && <span>{copiedLabel}</span>}
      </span>
    </button>
  );
}
