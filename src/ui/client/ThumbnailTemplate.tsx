import React, { useEffect, useState } from "react";
import { api } from "./lib";

type Cfg = Record<string, any>;

const num = (v: any, fallback: number) => {
  const n = parseFloat(String(v ?? "").replace(/[^0-9.]/g, ""));
  return Number.isFinite(n) ? n : fallback;
};

const labelStyle: React.CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: "0.5px", textTransform: "uppercase", color: "var(--text2)", marginBottom: 6, display: "block" };

const SCALE = 320 / 1080; // preview stage is 320px wide; thumbnails are 1080px

export default function ThumbnailTemplate() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api("/thumbnail-config").then((d) => setCfg(d && typeof d === "object" ? d : {})).catch(() => setCfg({}));
  }, []);

  if (!cfg) {
    return <div className="app"><div style={{ display: "flex", gap: 10, alignItems: "center", color: "var(--text2)", padding: 40 }}><div className="spinner sm" /> Loading…</div></div>;
  }

  const set = (k: string, v: any) => setCfg({ ...cfg, [k]: v });
  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api("/thumbnail-config", { method: "PUT", body: JSON.stringify(cfg) });
      if (r.error) throw new Error(r.error);
      setMsg("Saved — new thumbnails use this template");
    } catch (e: any) { setMsg(`Save failed: ${e.message}`); } finally { setBusy(false); }
  };

  const bg = cfg.bg_color || "#0D0D0D";
  const accent = cfg.accent_color || "#35C7C6";
  const headline = cfg.text_color || "#FFFFFF";
  const hlText = cfg.line2_text_color || "#0D0D0D";
  const boxY = num(cfg.box_y, 75);
  const l1Size = num(cfg.line1_font_size, 74);
  const l2Size = num(cfg.line2_font_size, 70);
  const border = num(cfg.frame_border_width, 3);
  const l1Upper = cfg.line1_uppercase !== false;
  const l2Italic = (cfg.line2_font_style || "italic") === "italic";

  const colorRow = (label: string, key: string, fallback: string) => (
    <div>
      <label style={labelStyle}>{label}</label>
      <div className="thumb-swatch-row">
        <input type="color" value={cfg[key] || fallback} onChange={(e) => set(key, e.target.value)} />
        <input type="text" value={cfg[key] || fallback} onChange={(e) => set(key, e.target.value)} style={{ fontSize: 12, padding: "7px 10px", width: 110, fontFamily: "var(--font-mono, monospace)" }} />
      </div>
    </div>
  );

  return (
    <div className="app">
      <div className="header"><h1>Thumbnail template</h1></div>

      <div className="clip-media">
        <div className="clip-media-col">
          <label style={labelStyle}>Preview</label>
          <div className="thumb-stage" style={{ background: bg, border: `${border}px solid ${accent}` }}>
            <div style={{ position: "absolute", inset: 0, background: `linear-gradient(to top, ${bg} 0%, transparent 45%)` }} />
            <div style={{
              position: "absolute", left: `${num(cfg.box_x, 55) * SCALE}px`, right: `${num(cfg.box_x, 55) * SCALE}px`,
              top: `${boxY}%`, background: cfg.box_fill_color || "rgba(13,13,13,0.85)",
              border: `${num(cfg.box_border_width, 3)}px solid ${accent}`, padding: `${10}px ${14}px`, borderRadius: 4,
            }}>
              <div style={{ fontSize: l1Size * SCALE, fontWeight: 700, color: headline, textTransform: l1Upper ? "uppercase" : "none", lineHeight: 1.15, letterSpacing: 1 }}>
                Intelligence is now
              </div>
              <div style={{ marginTop: 4 }}>
                <span style={{ fontSize: l2Size * SCALE, fontWeight: 600, fontStyle: l2Italic ? "italic" : "normal", background: accent, color: hlText, padding: "2px 8px", textTransform: l1Upper ? "uppercase" : "none", lineHeight: 1.3, display: "inline-block" }}>
                  a commodity
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="clip-media-col" style={{ width: 360, flex: 1 }}>
          <label style={labelStyle}>Brand template</label>
          <div className="thumb-fields">
            {colorRow("Background", "bg_color", "#0D0D0D")}
            {colorRow("Headline", "text_color", "#FFFFFF")}
            {colorRow("Accent / highlight", "accent_color", "#35C7C6")}
            {colorRow("Highlight text", "line2_text_color", "#0D0D0D")}
            <div className="full">
              <label style={labelStyle}>Box position — {boxY}% from top</label>
              <input type="range" min={30} max={88} value={boxY} onChange={(e) => set("box_y", `${e.target.value}%`)} style={{ width: "100%" }} />
            </div>
            <div>
              <label style={labelStyle}>Headline size (px)</label>
              <input type="number" value={l1Size} onChange={(e) => set("line1_font_size", `${e.target.value}px`)} style={{ fontSize: 13, padding: "8px 10px", width: 90 }} />
            </div>
            <div>
              <label style={labelStyle}>Highlight size (px)</label>
              <input type="number" value={l2Size} onChange={(e) => set("line2_font_size", `${e.target.value}px`)} style={{ fontSize: 13, padding: "8px 10px", width: 90 }} />
            </div>
            <div>
              <label style={labelStyle}>Frame border (px)</label>
              <input type="number" value={border} onChange={(e) => set("frame_border_width", parseInt(e.target.value) || 0)} style={{ fontSize: 13, padding: "8px 10px", width: 90 }} />
            </div>
            <div className="full" style={{ display: "flex", gap: 20 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--text2)" }}>
                <input type="checkbox" checked={l1Upper} onChange={(e) => set("line1_uppercase", e.target.checked)} /> Uppercase
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--text2)" }}>
                <input type="checkbox" checked={l2Italic} onChange={(e) => set("line2_font_style", e.target.checked ? "italic" : "normal")} /> Italic highlight
              </label>
            </div>
          </div>
          <div style={{ marginTop: 16 }}>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={busy}>{busy ? <div className="spinner sm" /> : "Save template"}</button>
          </div>
          {msg && <div className="set-note ok" style={{ marginTop: 12 }}>{msg}</div>}
        </div>
      </div>
    </div>
  );
}
