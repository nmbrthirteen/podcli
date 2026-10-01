import React, { useState } from "react";
import { fmt } from "./lib";
import type { McSession } from "./multicam-types";

// Mirrors the clamps in multicam.update_mapping so the inputs never show a value the server won't use.
const LIMITS = {
  min_shot: { min: 0.5, max: 10, step: 0.5 },
  max_shot: { min: 0, max: 600, step: 1 },
  wide_insert: { min: 1, max: 15, step: 0.5 },
  backchannel: { min: 0, max: 5, step: 0.1 },
};

export default function MulticamAutoCut({
  session,
  locked,
  onRecut,
}: {
  session: McSession;
  locked: boolean;
  onRecut: (extra: Record<string, unknown>) => void;
}) {
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
    <div style={{ marginTop: 12 }}>
      <button className="btn btn-ghost btn-sm" onClick={() => setOpen((v) => !v)}>
        {open ? "Hide auto-cut settings" : "Auto-cut settings"}
      </button>
      {open && (
        <>
          <div className="row" style={{ margin: "12px 0" }}>
            {field(`Episode start (${fmt(range.range_start)})`, range.range_start, (v) => setRange({ ...range, range_start: v }))}
            {field(`Episode end (${fmt(range.range_end)})`, range.range_end, (v) => setRange({ ...range, range_end: v }))}
            {field("Shortest shot (s)", settings.min_shot, (v) => setSettings({ ...settings, min_shot: v }), LIMITS.min_shot)}
            {field("Wide cutaway after (s, 0 = never)", settings.max_shot, (v) => setSettings({ ...settings, max_shot: v }), LIMITS.max_shot)}
            {field("Wide cutaway length (s)", settings.wide_insert, (v) => setSettings({ ...settings, wide_insert: v }), LIMITS.wide_insert)}
            {field("Ignore interjections under (s)", settings.backchannel, (v) => setSettings({ ...settings, backchannel: v }), LIMITS.backchannel)}
          </div>
          <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <input type="checkbox" checked={settings.hold_guest} onChange={(e) => setSettings({ ...settings, hold_guest: e.target.checked })} />
            Stay on the guest for their whole answer
          </label>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              className="btn btn-primary btn-sm"
              disabled={locked}
              onClick={() => onRecut({ ...range, cut_settings: settings })}
            >
              Cut again
            </button>
          </div>
        </>
      )}
    </div>
  );
}
