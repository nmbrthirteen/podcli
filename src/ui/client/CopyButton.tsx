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
  failedLabel?: string;
};

export default function CopyButton({
  text,
  getText,
  label = "Copy",
  copiedLabel = "Copied",
  failedLabel = "Copy failed",
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
  const [failed, setFailed] = useState(false);
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

    if (timerRef.current) window.clearTimeout(timerRef.current);
    try {
      await copyText(value);
      setFailed(false);
      setCopied(true);
      onCopied?.();
      timerRef.current = window.setTimeout(() => setCopied(false), resetMs);
    } catch {
      setCopied(false);
      setFailed(true);
      timerRef.current = window.setTimeout(() => setFailed(false), resetMs);
    }
  };

  return (
    <button
      type="button"
      className={`${className} copy-button ${copied ? "is-copied" : ""} ${failed ? "is-copy-failed" : ""} ${iconOnly ? "is-icon-only" : ""}`}
      onClick={handleCopy}
      disabled={disabled}
      title={failed ? failedLabel : title ?? label}
      aria-label={copied ? copiedLabel : failed ? failedLabel : label}
      aria-live="polite"
      style={style}
    >
      <span className="copy-button-layer copy-button-idle">
        <Copy className="copy-button-icon" aria-hidden="true" />
        {!iconOnly && <span>{failed ? failedLabel : label}</span>}
      </span>
      <span className="copy-button-layer copy-button-success">
        <Check className="copy-button-icon" aria-hidden="true" />
        {!iconOnly && <span>{copiedLabel}</span>}
      </span>
    </button>
  );
}
