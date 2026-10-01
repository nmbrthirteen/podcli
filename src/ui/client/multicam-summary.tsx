import React from "react";
import { fmt } from "./lib";
import MulticamAutoCut from "./multicam-auto-cut";
import type { McEdit, McSession } from "./multicam-types";
import { cameraColorMap, whoLabel } from "./multicam-types";

export default function MulticamSummary({
  session,
  locked,
  onEdit,
  onRecut,
}: {
  session: McSession;
  locked: boolean;
  onEdit: McEdit;
  onRecut: (extra: Record<string, unknown>) => void;
}) {
  const cameras = session.sources.filter((s) => s.role === "camera" && s.offset !== null);
  const colors = cameraColorMap(cameras.map((c) => c.id));
  const { shots = 0, average_shot = 0, duration = 0, share = {} } = session.stats;

  return (
    <div className="section card">
      <div className="card-title" style={{ marginBottom: 4 }}>Cut</div>
      <p className="card-desc">
        {session.resolved_style === "remote"
          ? "Questions play on the split screen; the guest goes full frame on long answers."
          : "Cameras follow whoever is speaking, picked from each person's mic."}
      </p>

      <div aria-hidden style={{ display: "flex", height: 28, borderRadius: "var(--radius-sm)", overflow: "hidden", border: "1px solid var(--border)" }}>
        {session.cuts.map((c) => (
          <div
            key={`${c.start}-${c.source_id}`}
            title={`${fmt(c.start)}-${fmt(c.end)}`}
            style={{ flex: `${Math.max(0.2, c.end - c.start)} 0 0`, background: colors[c.source_id], opacity: 0.85, borderRight: "1px solid var(--bg)" }}
          />
        ))}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", marginTop: 10 }}>
        {cameras.map((cam) => (
          <span key={cam.id} className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 10, height: 10, borderRadius: 3, background: colors[cam.id] }} />
            {whoLabel(session.people, cam)} {Math.round((share[cam.id] || 0) * 100)}%
          </span>
        ))}
        <span className="hint">
          {shots} shots · average {average_shot.toFixed(1)}s · {fmt(duration)}
          {session.removals.length > 0 &&
            ` · ${session.removals.length} cut out (${fmt(session.removals.reduce((t, r) => t + r.end - r.start, 0))})`}
        </span>
        <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 6, marginLeft: "auto" }}>
          Look
          <select value={session.look} disabled={locked} onChange={(e) => onEdit({ action: "map", look: e.target.value })} style={{ width: "auto", padding: "4px 28px 4px 10px", fontSize: 12 }}>
            {session.looks.map((l) => <option key={l} value={l}>{l[0].toUpperCase() + l.slice(1)}</option>)}
          </select>
        </label>
      </div>

      <MulticamAutoCut
        key={JSON.stringify([session.range_start, session.range_end, session.cut_settings])}
        session={session}
        locked={locked}
        onRecut={onRecut}
      />
    </div>
  );
}
