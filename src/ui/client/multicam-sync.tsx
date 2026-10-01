import React, { useState } from "react";
import { basename } from "./lib";
import type { McEdit, McPerson, McSession, McSource } from "./multicam-types";
import { whoLabel } from "./multicam-types";

const STATUS: Record<string, { label: string; color: string; pill: string }> = {
  reference: { label: "Reference", color: "var(--green)", pill: "pill-green" },
  ok: { label: "Synced", color: "var(--green)", pill: "pill-green" },
  rough: { label: "Rough sync", color: "var(--amber)", pill: "pill-amber" },
  manual: { label: "Set by hand", color: "var(--blue)", pill: "pill-sky" },
  assumed: { label: "Starts with the others", color: "var(--amber)", pill: "pill-amber" },
};
const NOT_SYNCED = { label: "Not synced", color: "var(--red)", pill: "pill-red" };

export default function MulticamSync({
  session,
  locked,
  onEdit,
  onCut,
}: {
  session: McSession;
  locked: boolean;
  onEdit: McEdit;
  onCut: () => void;
}) {
  const duration = session.timeline_duration || 1;
  const active = session.sources.filter((s) => s.role !== "ignore" && !s.parent && !s.members.length);
  const ready = active.every((s) => s.offset !== null);

  return (
    <div className="section card">
      <div className="card-title" style={{ marginBottom: 4 }}>Sync</div>
      <p className="card-desc">Every file on one timeline. Fix anything that didn't sync before cutting.</p>

      <div style={{ position: "relative", height: active.length * 34 + 8, background: "var(--surface2)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)", marginBottom: 12 }}>
        {active.map((source, i) => {
          const start = source.timeline_start ?? 0;
          const end = source.timeline_end ?? start;
          return (
            <div
              key={source.id}
              title={`${basename(source.path)} (${whoLabel(session.people, source)})`}
              style={{
                position: "absolute", top: i * 34 + 4, left: `${(start / duration) * 100}%`,
                width: `${Math.max(0.5, ((end - start) / duration) * 100)}%`, height: 24,
                background: (STATUS[source.sync.status || ""] || NOT_SYNCED).color, opacity: 0.85, borderRadius: 4,
                display: "flex", alignItems: "center", padding: "0 8px", fontSize: 11, color: "var(--bg)",
                fontWeight: 600, overflow: "hidden", whiteSpace: "nowrap",
              }}
            >
              {basename(source.path)}
            </div>
          );
        })}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {active.map((source) => (
          <SyncRow key={source.id} source={source} people={session.people} onEdit={onEdit} disabled={locked} />
        ))}
      </div>

      {session.cuts.length === 0 && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
          <button className="btn btn-primary" disabled={locked || !ready} onClick={onCut}>Cut the episode</button>
        </div>
      )}
    </div>
  );
}

function SyncRow({ source, people, onEdit, disabled }: { source: McSource; people: McPerson[]; onEdit: McEdit; disabled: boolean }) {
  const status = STATUS[source.sync.status || ""] || NOT_SYNCED;
  const [draft, setDraft] = useState("");
  const frame = 1 / (source.fps || 30);
  const drift = source.sync.drift_ppm;
  const edit = (change: Record<string, number>) => onEdit({ action: "map", sources: [{ id: source.id, ...change }] });

  function saveOffset() {
    const offset = Number(draft);
    if (draft.trim() === "" || !Number.isFinite(offset)) return;
    edit({ offset });
    setDraft("");
  }

  return (
    <div className="card" style={{ display: "flex", alignItems: "center", gap: 12, padding: 12, margin: 0, flexWrap: "wrap" }}>
      <div style={{ minWidth: 0, width: 220 }}>
        <div style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={source.path}>
          {basename(source.path)}
        </div>
        <div className="hint">{whoLabel(people, source)}</div>
      </div>

      <span className={`pill ${status.pill}`}>{status.label}</span>
      {typeof drift === "number" && Math.abs(drift) >= 5 && (
        <span className="hint">Clock drift {Math.round(drift)} ppm, corrected</span>
      )}
      {source.offset === null && (
        <span className="hint" style={{ color: "var(--red)" }}>{source.sync.message || "Sync this file, or type where it starts."}</span>
      )}
      {source.sync.status === "assumed" && <span className="hint">{source.sync.message}</span>}

      <div style={{ display: "inline-flex", gap: 6, marginLeft: "auto", alignItems: "center" }}>
        {source.offset === null ? (
          <>
            <input
              type="number"
              step={0.001}
              placeholder="Starts at (s)"
              value={draft}
              disabled={disabled}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") saveOffset(); }}
              style={{ width: 120 }}
            />
            <button className="btn btn-ghost btn-sm" disabled={disabled || draft.trim() === ""} onClick={saveOffset}>Set</button>
          </>
        ) : (
          <>
            <span className="hint" style={{ fontVariantNumeric: "tabular-nums" }}>{source.offset.toFixed(3)}s</span>
            <button className="btn btn-ghost btn-sm" disabled={disabled} onClick={() => edit({ nudge: -frame })}>-1 frame</button>
            <button className="btn btn-ghost btn-sm" disabled={disabled} onClick={() => edit({ nudge: frame })}>+1 frame</button>
          </>
        )}
      </div>
    </div>
  );
}
