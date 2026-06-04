import React, { useEffect, useState } from "react";
import { api } from "./lib";

type Cfg = Record<string, any>;
type FieldType = "color" | "num" | "text" | "bool" | "select";
interface Field { k: string; t: FieldType; opts?: string[]; wide?: boolean }

const GROUPS: { group: string; items: Field[] }[] = [
  { group: "Canvas", items: [
    { k: "enabled", t: "bool" }, { k: "width", t: "num" }, { k: "height", t: "num" },
    { k: "bg_color", t: "color" },
    { k: "font_family", t: "text", wide: true }, { k: "font_import_url", t: "text", wide: true },
  ]},
  { group: "Frame & box", items: [
    { k: "frame_border_width", t: "num" },
    { k: "box_x", t: "text" }, { k: "box_y", t: "text" },
    { k: "box_width", t: "text" }, { k: "box_min_height", t: "text" },
    { k: "box_border_width", t: "num" }, { k: "box_fill_color", t: "color" }, { k: "box_padding", t: "text" },
  ]},
  { group: "Headline — line 1", items: [
    { k: "text_color", t: "color" }, { k: "line1_color", t: "color" },
    { k: "line1_font_size", t: "text" }, { k: "line1_font_weight", t: "num" },
    { k: "line1_letter_spacing", t: "text" }, { k: "line1_line_height", t: "num" },
    { k: "line1_margin_bottom", t: "text" }, { k: "line1_uppercase", t: "bool" }, { k: "line1_nowrap", t: "bool" },
  ]},
  { group: "Highlight — line 2", items: [
    { k: "accent_color", t: "color" }, { k: "line2_text_color", t: "color" },
    { k: "line2_font_size", t: "text" }, { k: "line2_font_weight", t: "num" },
    { k: "line2_font_style", t: "select", opts: ["italic", "normal"] },
    { k: "line2_letter_spacing", t: "text" }, { k: "line2_line_height", t: "num" },
    { k: "line2_uppercase", t: "bool" }, { k: "line2_highlight_padding", t: "text" },
  ]},
  { group: "Photo & gradient", items: [
    { k: "photo_brightness", t: "num" }, { k: "photo_object_position", t: "text" },
    { k: "gradient_top_height", t: "text" }, { k: "gradient_top_start_color", t: "color" }, { k: "gradient_top_end_color", t: "color" },
    { k: "gradient_bottom_start", t: "text" }, { k: "gradient_bottom_end_color", t: "color" }, { k: "gradient_bottom_fade_point", t: "text" },
  ]},
  { group: "Logo", items: [
    { k: "logo_position", t: "select", opts: ["bottom-center", "bottom-left", "bottom-right", "top-center", "top-left", "top-right"] },
    { k: "logo_height", t: "text" }, { k: "logo_margin", t: "text" }, { k: "logo_opacity", t: "num" },
  ]},
  { group: "Variations", items: [
    { k: "variations", t: "num" }, { k: "variation_offset_up", t: "text" }, { k: "variation_offset_down", t: "text" },
  ]},
];

const KNOWN = new Set(GROUPS.flatMap((g) => g.items.map((i) => i.k)));
const labelStyle: React.CSSProperties = { fontSize: 11, fontWeight: 600, letterSpacing: "0.3px", color: "var(--text2)", marginBottom: 5, display: "block" };
const titleCase = (k: string) => k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const isHex = (v: any) => typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v);
const px = (v: any, s: number, f = 0) => { const n = parseFloat(String(v ?? "").replace(/[^0-9.]/g, "")); return (Number.isFinite(n) ? n : f) * s; };
const isPct = (v: any) => typeof v === "string" && v.trim().endsWith("%");
const SCALE = 300 / 1080;

function padToPreview(pad: any): string {
  const parts = String(pad ?? "30px 45px").trim().split(/\s+/).map((p) => `${px(p, SCALE)}px`);
  return parts.join(" ") || "8px 12px";
}

export default function ThumbnailTemplate() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api("/thumbnail-config").then((d) => setCfg(d && typeof d === "object" ? d : {})).catch(() => setCfg({}));
  }, []);

  if (!cfg) return <div className="app"><div style={{ display: "flex", gap: 10, alignItems: "center", color: "var(--text2)", padding: 40 }}><div className="spinner sm" /> Loading…</div></div>;

  const set = (k: string, v: any) => setCfg({ ...cfg, [k]: v });
  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api("/thumbnail-config", { method: "PUT", body: JSON.stringify(cfg) });
      if (r.error) throw new Error(r.error);
      setMsg("Saved — new thumbnails use this template");
    } catch (e: any) { setMsg(`Save failed: ${e.message}`); } finally { setBusy(false); }
  };

  const field = (f: Field) => {
    const v = cfg[f.k];
    if (f.t === "bool") return (
      <label key={f.k} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text2)", paddingTop: 18 }}>
        <input type="checkbox" checked={v !== false && v !== undefined ? !!v : false} onChange={(e) => set(f.k, e.target.checked)} /> {titleCase(f.k)}
      </label>
    );
    return (
      <div key={f.k} className={f.wide ? "full" : ""}>
        <label style={labelStyle}>{titleCase(f.k)}</label>
        {f.t === "color" ? (
          <div className="thumb-swatch-row">
            {isHex(v) && <input type="color" value={v} onChange={(e) => set(f.k, e.target.value)} />}
            {!isHex(v) && <span style={{ width: 28, height: 28, borderRadius: 6, border: "1px solid var(--border)", background: v || "transparent", flexShrink: 0 }} />}
            <input type="text" value={v ?? ""} onChange={(e) => set(f.k, e.target.value)} style={{ fontSize: 12, padding: "7px 10px", flex: 1, minWidth: 0, fontFamily: "var(--font-mono, monospace)" }} />
          </div>
        ) : f.t === "select" ? (
          <select value={v ?? ""} onChange={(e) => set(f.k, e.target.value)}>{f.opts!.map((o) => <option key={o} value={o}>{o}</option>)}</select>
        ) : (
          <input type={f.t === "num" ? "number" : "text"} value={v ?? ""} step="any"
            onChange={(e) => set(f.k, f.t === "num" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)}
            style={{ width: "100%", fontSize: 13, padding: "8px 11px" }} />
        )}
      </div>
    );
  };

  const others = Object.keys(cfg).filter((k) => !KNOWN.has(k) && !k.startsWith("_"));

  // ── Live preview ──
  const bg = cfg.bg_color || "#0D0D0D";
  const accent = cfg.accent_color || "#35C7C6";
  const headline = cfg.line1_color || cfg.text_color || "#FFFFFF";
  const boxTop = isPct(cfg.box_y) ? cfg.box_y : "75%";

  return (
    <div className="app">
      <div className="header"><h1>Thumbnail template</h1></div>

      <div className="tmpl-layout">
        <div className="tmpl-preview">
          <label style={labelStyle}>Preview</label>
          <div className="thumb-stage" style={{ background: bg, border: `${px(cfg.frame_border_width, SCALE, 3)}px solid ${accent}` }}>
            <div style={{ position: "absolute", inset: 0, background: `linear-gradient(to top, ${cfg.gradient_bottom_end_color || bg} 0%, transparent 50%)` }} />
            <div style={{
              position: "absolute", left: `${px(cfg.box_x, SCALE, 55)}px`, right: `${px(cfg.box_x, SCALE, 55)}px`, top: boxTop,
              background: cfg.box_fill_color || "rgba(13,13,13,0.85)",
              border: `${px(cfg.box_border_width, SCALE, 3)}px solid ${accent}`, padding: padToPreview(cfg.box_padding),
            }}>
              <div style={{ fontSize: px(cfg.line1_font_size, SCALE, 74), fontWeight: Number(cfg.line1_font_weight) || 700, color: headline, textTransform: cfg.line1_uppercase !== false ? "uppercase" : "none", lineHeight: Number(cfg.line1_line_height) || 1.15, letterSpacing: px(cfg.line1_letter_spacing, SCALE, 2), marginBottom: px(cfg.line1_margin_bottom, SCALE, 10) }}>
                Intelligence is now
              </div>
              <span style={{ fontSize: px(cfg.line2_font_size, SCALE, 70), fontWeight: Number(cfg.line2_font_weight) || 600, fontStyle: (cfg.line2_font_style || "italic") === "italic" ? "italic" : "normal", background: accent, color: cfg.line2_text_color || "#0D0D0D", padding: padToPreview(cfg.line2_highlight_padding || "6px 20px"), textTransform: cfg.line2_uppercase !== false ? "uppercase" : "none", letterSpacing: px(cfg.line2_letter_spacing, SCALE, 1), display: "inline-block", lineHeight: Number(cfg.line2_line_height) || 1.15 }}>
                a commodity
              </span>
            </div>
          </div>
          <div style={{ marginTop: 16 }}>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={busy}>{busy ? <div className="spinner sm" /> : "Save template"}</button>
          </div>
          {msg && <div className="set-note ok" style={{ marginTop: 12 }}>{msg}</div>}
        </div>

        <div className="tmpl-fields">
          {GROUPS.map((g) => (
            <div key={g.group} className="section">
              <div className="section-label">{g.group}</div>
              <div className="thumb-fields">{g.items.map(field)}</div>
            </div>
          ))}
          {others.length > 0 && (
            <div className="section">
              <div className="section-label">Other</div>
              <div className="thumb-fields">{others.map((k) => field({ k, t: typeof cfg[k] === "number" ? "num" : "text" }))}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
