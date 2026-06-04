import React, { useEffect, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { api, fmt, basename } from "./lib";

interface Clip {
  id: string;
  source_video: string;
  title: string;
  caption_style: string;
  crop_strategy: string;
  start_second: number;
  end_second: number;
  duration: number;
  file_size_mb?: number;
  output_path: string;
  created_at: string;
  content_type?: string;
  transcript_slice?: string;
}

const CAPTION_STYLES = ["branded", "hormozi", "karaoke", "subtle"];

export default function ClipDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [clip, setClip] = useState<Clip | null>(null);
  const [loading, setLoading] = useState(true);
  const [title, setTitle] = useState("");
  const [captionStyle, setCaptionStyle] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [previewBust, setPreviewBust] = useState(0);

  const load = () => {
    api("/history?limit=500")
      .then((d: Clip[]) => {
        const found = Array.isArray(d)
          ? d.find((c) => c.id === id || String(c.id).startsWith(id || "\0"))
          : null;
        setClip(found || null);
        if (found) {
          setTitle(found.title);
          setCaptionStyle(found.caption_style);
        }
      })
      .finally(() => setLoading(false));
  };
  useEffect(load, [id]);

  if (loading) {
    return (
      <div className="app">
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text2)", padding: 40 }}>
          <div className="spinner sm" /> Loading clip…
        </div>
      </div>
    );
  }
  if (!clip) {
    return (
      <div className="app">
        <div className="header"><h1>Clip not found</h1></div>
        <Link to="/" style={{ color: "var(--accent)" }}>← Back to studio</Link>
      </div>
    );
  }

  const dirty = title !== clip.title || captionStyle !== clip.caption_style;
  const previewFile = basename(clip.output_path);
  const previewUrl = `/api/preview/${previewFile}${previewBust ? `?t=${previewBust}` : ""}`;

  const save = async () => {
    setBusy("save"); setMsg(null);
    try {
      const r = await api(`/clips/${clip.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title, caption_style: captionStyle }),
      });
      if (r.error) throw new Error(r.error);
      setMsg("Saved");
      load();
    } catch (e: any) {
      setMsg(`Save failed: ${e.message}`);
    } finally {
      setBusy(null);
    }
  };

  const regenThumbnail = async () => {
    setBusy("thumb"); setMsg(null);
    try {
      const r = await api(`/clips/${clip.id}/thumbnail`, { method: "POST", body: "{}" });
      if (r.error) throw new Error(r.error);
      setMsg("Thumbnail regenerated");
      setPreviewBust(Date.now());
    } catch (e: any) {
      setMsg(`Thumbnail failed: ${e.message}`);
    } finally {
      setBusy(null);
    }
  };

  const reopen = async () => {
    setBusy("reopen"); setMsg(null);
    try {
      const r = await api(`/clips/${clip.id}/reopen`, { method: "POST", body: "{}" });
      if (r.error) throw new Error(r.error);
      navigate("/episode");
    } catch (e: any) {
      setMsg(`Reopen failed: ${e.message}`);
      setBusy(null);
    }
  };

  const fieldLabel = { fontSize: 11, fontWeight: 700, letterSpacing: "0.5px", textTransform: "uppercase", color: "var(--text2)", marginBottom: 6, display: "block" } as React.CSSProperties;

  return (
    <div className="app">
      <div className="header">
        <Link to="/" style={{ fontSize: 12, color: "var(--text3)", textDecoration: "none" }}>← Library</Link>
        <h1 style={{ marginTop: 8 }}>{clip.title}</h1>
      </div>

      <div className="layout">
        <div className="main-col">
          <div className="section">
            <label style={fieldLabel}>Title</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)}
              style={{ width: "100%", fontSize: 14, padding: "10px 13px" }} />
          </div>

          <div className="section">
            <label style={fieldLabel}>Caption style</label>
            <select value={captionStyle} onChange={(e) => setCaptionStyle(e.target.value)}>
              {CAPTION_STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 6 }}>
              Changing the caption style updates metadata only. Re-render in the editor to apply it to the video.
            </div>
          </div>

          <div className="section">
            <label style={fieldLabel}>Details</label>
            <div style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.7 }}>
              <div>Source · {basename(clip.source_video)}</div>
              <div>Range · {fmt(clip.start_second)} → {fmt(clip.end_second)} ({clip.duration}s)</div>
              <div>Crop · {clip.crop_strategy}</div>
              {clip.content_type && <div>Type · {clip.content_type}</div>}
              {clip.file_size_mb != null && <div>Size · {clip.file_size_mb.toFixed(1)}MB</div>}
            </div>
            {clip.transcript_slice && (
              <div style={{ marginTop: 10, padding: "10px 12px", background: "var(--surface)", borderRadius: "var(--radius-sm)", fontSize: 12, color: "var(--text2)", lineHeight: 1.6, fontStyle: "italic" }}>
                “{clip.transcript_slice}”
              </div>
            )}
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={!dirty || busy !== null}>
              {busy === "save" ? <div className="spinner sm" /> : "Save"}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={regenThumbnail} disabled={busy !== null}>
              {busy === "thumb" ? <div className="spinner sm" /> : "Regenerate thumbnail"}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={reopen} disabled={busy !== null}>
              {busy === "reopen" ? <div className="spinner sm" /> : "Reopen in editor"}
            </button>
            {msg && <span style={{ fontSize: 12, color: "var(--text2)", alignSelf: "center" }}>{msg}</span>}
          </div>
        </div>

        <div className="preview-col">
          <div className="preview-panel">
            {previewFile ? (
              <video key={previewUrl} src={previewUrl} controls preload="auto" className="vertical" style={{ width: "100%", borderRadius: "var(--radius)" }} />
            ) : (
              <div className="phone-empty">No rendered output</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
