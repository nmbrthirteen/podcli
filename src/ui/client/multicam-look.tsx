import React from "react";
import type { McEdit, McSession } from "./multicam-types";
import { mcImageUrl } from "./multicam-types";

export default function MulticamLook({
  session,
  lookPreviews,
  locked,
  onEdit,
}: {
  session: McSession;
  lookPreviews: Record<string, string>;
  locked: boolean;
  onEdit: McEdit;
}) {
  return (
    <div className="section card">
      <div className="card-title" style={{ marginBottom: 4 }}>Look</div>
      <p className="card-desc">Pick a color grade for the whole episode.</p>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {session.looks.map((look) => (
          <button
            key={look}
            className="card"
            disabled={locked}
            aria-pressed={session.look === look}
            onClick={() => onEdit({ action: "map", look })}
            style={{
              margin: 0, padding: 8, width: 160, cursor: locked ? "default" : "pointer", color: "var(--text)",
              borderColor: session.look === look ? "var(--accent)" : "var(--border)",
            }}
          >
            <div style={{ width: "100%", aspectRatio: "16/9", borderRadius: "var(--radius-sm)", background: "var(--surface2)", overflow: "hidden", marginBottom: 8 }}>
              {lookPreviews[look] && <img src={mcImageUrl(lookPreviews[look])} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />}
            </div>
            <div style={{ fontSize: 13, fontWeight: 600, textAlign: "center" }}>{look[0].toUpperCase() + look.slice(1)}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
