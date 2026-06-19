import React, { useEffect, useRef, useState } from "react";

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

function CopyIcon() {
  return (
    <svg className="copy-button-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M8 8h10v12H8z" />
      <path d="M6 16H4V4h12v2" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg className="copy-button-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}

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
      await navigator.clipboard.writeText(value);
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
        <CopyIcon />
        {!iconOnly && <span>{label}</span>}
      </span>
      <span className="copy-button-layer copy-button-success">
        <CheckIcon />
        {!iconOnly && <span>{copiedLabel}</span>}
      </span>
    </button>
  );
}
