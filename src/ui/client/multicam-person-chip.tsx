import React from "react";
import { TrashIcon } from "./icons";

export default function PersonChip({
  value,
  onChange,
  onRemove,
  onBlur,
  role,
  onRole,
}: {
  value: string;
  onChange: (name: string) => void;
  onRemove: () => void;
  onBlur?: () => void;
  role?: "host" | "guest";
  onRole?: (role: "host" | "guest") => void;
}) {
  return (
    <div className="file-badge" style={{ padding: "6px 10px", background: "var(--surface2)", border: "1px solid var(--border)" }}>
      <input
        value={value}
        aria-label="Person name"
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
        size={Math.max(4, value.length + 1)}
        style={{ border: "none", background: "none", width: "auto", color: "var(--text)" }}
      />
      {role && onRole && (
        <button
          className={`pill ${role === "guest" ? "pill-sky" : "pill-blue"}`}
          title={role === "guest" ? "Guest: answers stay on their camera. Click to make a host." : "Host. Click to make a guest."}
          onClick={() => onRole(role === "guest" ? "host" : "guest")}
          style={{ border: "none", cursor: "pointer" }}
        >
          {role === "guest" ? "Guest" : "Host"}
        </button>
      )}
      <button className="btn btn-ghost btn-sm" aria-label={`Remove ${value || "person"}`} onClick={onRemove} style={{ padding: "2px 6px" }}>
        <TrashIcon size={12} />
      </button>
    </div>
  );
}
