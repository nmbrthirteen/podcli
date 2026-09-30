import React, { useEffect, useRef, useState } from "react";
import { fmt } from "./lib";
import type { McEdit, McSession } from "./multicam-types";
import { cameraColorMap, mcStreamUrl, whoLabel } from "./multicam-types";

// Mirrors the clamps in multicam.update_mapping so the inputs never show a value the server won't use.
const LIMITS = {
  min_shot: { min: 0.5, max: 10, step: 0.5 },
  max_shot: { min: 0, max: 600, step: 1 },
  wide_insert: { min: 1, max: 15, step: 0.5 },
};

export default function MulticamCut({
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
  const [selected, setSelected] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);

  const cameras = session.sources.filter((s) => s.role === "camera" && s.offset !== null);
  const colors = cameraColorMap(cameras.map((c) => c.id));
  const index = Math.min(selected, session.cuts.length - 1);
  const shot = session.cuts[index];
  const shotCamera = cameras.find((c) => c.id === shot?.source_id);

  useEffect(() => {
    if (!shot || !shotCamera || !videoRef.current) return;
    videoRef.current.currentTime = Math.max(0, (shot.start - (shotCamera.timeline_start ?? 0)) / (shotCamera.speed || 1));
  }, [index, shot?.start, shot?.source_id]);

  return (
    <div className="section card">
      <div className="card-title" style={{ marginBottom: 4 }}>Cut</div>
      <p className="card-desc">Cameras follow whoever is speaking. Click a shot to swap its camera.</p>

      {!session.has_person_mics && (
        <div className="hint" style={{ marginBottom: 12 }}>
          No personal mics mapped, so cuts follow speaker detection on the shared audio. Map one mic per person for better cuts.
        </div>
      )}

      <div style={{ display: "flex", height: 36, borderRadius: "var(--radius-sm)", overflow: "hidden", border: "1px solid var(--border)" }}>
        {session.cuts.map((c, i) => (
          <button
            key={`${c.start}-${c.source_id}`}
            aria-label={`Shot ${i + 1}, ${fmt(c.start)}-${fmt(c.end)}`}
            onClick={() => setSelected(i)}
            style={{
              flex: `${Math.max(0.2, c.end - c.start)} 0 0`, minWidth: 0, padding: 0, border: "none",
              background: colors[c.source_id] || "var(--text3)", opacity: i === index ? 1 : 0.55, cursor: "pointer",
              borderRight: "1px solid var(--bg)",
            }}
          />
        ))}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, margin: "10px 0 16px" }}>
        {cameras.map((cam) => (
          <span key={cam.id} className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 10, height: 10, borderRadius: 3, background: colors[cam.id] }} />
            {whoLabel(session.people, cam)} {Math.round((session.stats.share?.[cam.id] || 0) * 100)}%
          </span>
        ))}
        <span className="hint" style={{ marginLeft: "auto" }}>
          {session.stats.shots} shots · average {(session.stats.average_shot || 0).toFixed(1)}s · {fmt(session.stats.duration || 0)}
        </span>
      </div>

      {shot && (
        <div className="card" style={{ padding: 14, marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
            <strong style={{ fontVariantNumeric: "tabular-nums" }}>Shot {index + 1} · {fmt(shot.start)}-{fmt(shot.end)}</strong>
            <div style={{ display: "flex", gap: 6, marginLeft: "auto", flexWrap: "wrap" }}>
              {cameras.map((c) => (
                <button
                  key={c.id}
                  className={`btn btn-sm ${c.id === shot.source_id ? "btn-primary" : "btn-ghost"}`}
                  disabled={locked || c.id === shot.source_id}
                  onClick={() => onEdit({ action: "cut", index, source_id: c.id })}
                >
                  {whoLabel(session.people, c)}
                </button>
              ))}
            </div>
          </div>
          {shotCamera && (
            <video ref={videoRef} src={mcStreamUrl(shotCamera.path)} controls style={{ width: "100%", borderRadius: "var(--radius-sm)", background: "#000" }} />
          )}
        </div>
      )}

      <CutSettings key={JSON.stringify([session.range_start, session.range_end, session.cut_settings])} session={session} locked={locked} onRecut={onRecut} />
    </div>
  );
}

function CutSettings({ session, locked, onRecut }: { session: McSession; locked: boolean; onRecut: (extra: Record<string, unknown>) => void }) {
  const [open, setOpen] = useState(false);
  const [range, setRange] = useState({ range_start: session.range_start ?? 0, range_end: session.range_end ?? session.timeline_duration });
  const [settings, setSettings] = useState(session.cut_settings);

  const field = (label: string, value: number, onChange: (v: number) => void, limits = { min: 0, max: session.timeline_duration, step: 0.1 }) => (
    <div>
      <label className="field-label">{label}</label>
      <input type="number" {...limits} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ width: "100%" }} />
    </div>
  );

  return (
    <>
      <button className="btn btn-ghost btn-sm" onClick={() => setOpen((v) => !v)}>
        {open ? "Hide cut settings" : "Cut settings"}
      </button>
      {open && (
        <>
          <div className="row" style={{ margin: "12px 0" }}>
            {field(`Episode start (${fmt(range.range_start)})`, range.range_start, (v) => setRange({ ...range, range_start: v }))}
            {field(`Episode end (${fmt(range.range_end)})`, range.range_end, (v) => setRange({ ...range, range_end: v }))}
            {field("Shortest shot (s)", settings.min_shot, (v) => setSettings({ ...settings, min_shot: v }), LIMITS.min_shot)}
            {field("Wide cutaway after (s, 0 = never)", settings.max_shot, (v) => setSettings({ ...settings, max_shot: v }), LIMITS.max_shot)}
            {field("Wide cutaway length (s)", settings.wide_insert, (v) => setSettings({ ...settings, wide_insert: v }), LIMITS.wide_insert)}
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button className="btn btn-primary btn-sm" disabled={locked} onClick={() => onRecut({ ...range, cut_settings: settings })}>
              Recut
            </button>
          </div>
        </>
      )}
    </>
  );
}
