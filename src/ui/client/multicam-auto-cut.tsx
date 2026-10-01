import React, { useState } from "react";
import { fmt } from "./lib";
import type { McCutSettings, McSession, McStyle } from "./multicam-types";

// Mirrors the clamps in multicam.update_mapping so the inputs never show a value the server won't use.
const LIMITS = {
  min_shot: { min: 0.5, max: 10, step: 0.5 },
  max_shot: { min: 0, max: 600, step: 1 },
  wide_insert: { min: 1, max: 15, step: 0.5 },
  backchannel: { min: 0, max: 5, step: 0.1 },
  guest_min: { min: 0, max: 60, step: 1 },
  guest_delay: { min: 0, max: 30, step: 0.5 },
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
    <label style={{ display: "block" }}>
      <span className="field-label">{label}</span>
      <input type="number" {...limits} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ width: "100%" }} />
    </label>
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
            {field("Guest answers stay wide under (s)", settings.guest_min, (v) => setSettings({ ...settings, guest_min: v }), LIMITS.guest_min)}
            {field("Open long answers wide for (s)", settings.guest_delay, (v) => setSettings({ ...settings, guest_delay: v }), LIMITS.guest_delay)}
          </div>
          <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center", marginBottom: 12 }}>
            <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              Style
              <select
                value={settings.style}
                onChange={(e) => setSettings({ ...settings, style: e.target.value as McStyle })}
                style={{ width: "auto", padding: "4px 28px 4px 10px", fontSize: 12 }}
              >
                <option value="auto">Automatic ({session.auto_style === "remote" ? "remote call" : "studio"})</option>
                <option value="studio">Studio cameras</option>
                <option value="remote">Remote call</option>
              </select>
            </label>
            <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <input type="checkbox" checked={settings.hold_guest} onChange={(e) => setSettings({ ...settings, hold_guest: e.target.checked })} />
              Stay on the guest for their whole answer
            </label>
            <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <input type="checkbox" checked={settings.host_solo} onChange={(e) => setSettings({ ...settings, host_solo: e.target.checked })} />
              Give hosts their own shot
            </label>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              className="btn btn-primary btn-sm"
              disabled={locked}
              onClick={() => {
                // Only what was changed is sent: anything else follows the style's own defaults.
                const changed = Object.fromEntries(
                  Object.entries(settings).filter(([k, v]) => v !== session.cut_settings[k as keyof McCutSettings]),
                );
                const rangeChanged = range.range_start !== (session.range_start ?? 0) || range.range_end !== (session.range_end ?? session.timeline_duration);
                onRecut({ ...(rangeChanged ? range : {}), cut_settings: changed });
              }}
            >
              Cut again
            </button>
          </div>
        </>
      )}
    </div>
  );
}
