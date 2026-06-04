import React, { useEffect, useRef, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { api, fmt, basename } from "./lib";
import ClipPlayer from "./ClipPlayer";

interface ThumbnailConfig {
  text?: string;
  image_path?: string;
  timestamp?: number;
  preview_path?: string;
}

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
  thumbnail_config?: ThumbnailConfig;
}

const CAPTION_STYLES = ["branded", "hormozi", "karaoke", "subtle"];
const labelStyle: React.CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: "0.5px", textTransform: "uppercase", color: "var(--text2)", marginBottom: 8, display: "block" };

export default function ClipDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const playerTime = useRef(0);

  const [clip, setClip] = useState<Clip | null>(null);
  const [loading, setLoading] = useState(true);
  const [title, setTitle] = useState("");
  const [captionStyle, setCaptionStyle] = useState("");
  const [thumbText, setThumbText] = useState("");
  const [thumbImage, setThumbImage] = useState<string | null>(null);
  const [thumbTimestamp, setThumbTimestamp] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [previewBust, setPreviewBust] = useState(0);
  const [davinciOn, setDavinciOn] = useState(false);

  useEffect(() => {
    api("/integrations")
      .then((d) => setDavinciOn(!!(d.integrations || []).find((i: any) => i.name === "davinci_resolve" && i.enabled)))
      .catch(() => {});
  }, []);

  const load = () => {
    api("/history?limit=500")
      .then((d: Clip[]) => {
        const found = Array.isArray(d) ? d.find((c) => c.id === id || String(c.id).startsWith(id || "\0")) : null;
        setClip(found || null);
        if (found) {
          setTitle(found.title);
          setCaptionStyle(found.caption_style);
          const tc = found.thumbnail_config || {};
          setThumbText(tc.text ?? found.title);
          setThumbImage(tc.image_path ?? null);
          setThumbTimestamp(tc.image_path ? null : tc.timestamp ?? null);
        }
      })
      .finally(() => setLoading(false));
  };
  useEffect(load, [id]);

  if (loading) {
    return (
      <div className="app">
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text2)", padding: 40 }}>
          <div className="spinner sm" /> Loading…
        </div>
      </div>
    );
  }
  if (!clip) {
    return (
      <div className="app">
        <div className="header"><h1>Clip not found</h1></div>
        <Link to="/" style={{ color: "var(--accent)" }}>← Library</Link>
      </div>
    );
  }

  const dirty = title !== clip.title || captionStyle !== clip.caption_style;
  const previewFile = basename(clip.output_path);
  const previewUrl = `/api/preview/${previewFile}${previewBust ? `?t=${previewBust}` : ""}`;

  const save = async () => {
    setBusy("save"); setMsg(null);
    try {
      const r = await api(`/clips/${clip.id}`, { method: "PATCH", body: JSON.stringify({ title, caption_style: captionStyle }) });
      if (r.error) throw new Error(r.error);
      setMsg("Saved");
      load();
    } catch (e: any) {
      setMsg(`Save failed: ${e.message}`);
    } finally {
      setBusy(null);
    }
  };

  const useCurrentFrame = () => {
    setThumbTimestamp(clip.start_second + playerTime.current);
    setThumbImage(null);
    setMsg(null);
  };

  const uploadImage = async (file: File) => {
    setBusy("upload"); setMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await (await fetch("/api/upload", { method: "POST", body: fd })).json();
      if (!r.file_path) throw new Error(r.error || "upload failed");
      setThumbImage(r.file_path);
      setThumbTimestamp(null);
    } catch (e: any) {
      setMsg(`Upload failed: ${e.message}`);
    } finally {
      setBusy(null);
    }
  };

  const applyThumbnail = async () => {
    setBusy("thumb"); setMsg(null);
    try {
      const cfg: ThumbnailConfig = { text: thumbText || undefined };
      if (thumbImage) cfg.image_path = thumbImage;
      else if (thumbTimestamp != null) cfg.timestamp = thumbTimestamp;
      const p = await api(`/clips/${clip.id}`, { method: "PATCH", body: JSON.stringify({ thumbnail_config: cfg }) });
      if (p.error) throw new Error(p.error);
      const r = await api(`/clips/${clip.id}/thumbnail`, { method: "POST", body: "{}" });
      if (r.error) throw new Error(r.error);
      setMsg("Thumbnail applied");
      setPreviewBust(Date.now());
      load();
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

  const exportDavinci = async () => {
    setBusy("davinci"); setMsg(null);
    try {
      const r = await api(`/clips/${clip.id}/davinci`, { method: "POST", body: "{}" });
      if (r.error) throw new Error(r.error);
      setMsg(r.path ? `DaVinci project written: ${r.path}` : "DaVinci project exported");
    } catch (e: any) {
      setMsg(`DaVinci export failed: ${e.message}`);
    } finally {
      setBusy(null);
    }
  };

  const source = thumbImage ? `Image · ${basename(thumbImage)}` : thumbTimestamp != null ? `Frame @ ${fmt(thumbTimestamp)}` : "Auto (best frame)";

  return (
    <div className="app">
      <div className="header">
        <Link to="/" style={{ fontSize: 12, color: "var(--text3)", textDecoration: "none" }}>← Library</Link>
        <h1 style={{ marginTop: 8 }}>{clip.title}</h1>
      </div>

      <div className="layout">
        <div className="main-col">
          <div className="section">
            <label style={labelStyle}>Title</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: "100%", fontSize: 14, padding: "10px 13px" }} />
            <label style={{ ...labelStyle, marginTop: 16 }}>Caption style</label>
            <select value={captionStyle} onChange={(e) => setCaptionStyle(e.target.value)}>
              {CAPTION_STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <div style={{ marginTop: 16 }}>
              <button className="btn btn-primary btn-sm" onClick={save} disabled={!dirty || busy !== null}>
                {busy === "save" ? <div className="spinner sm" /> : "Save"}
              </button>
            </div>
          </div>

          <div className="section">
            <label style={labelStyle}>Thumbnail</label>
            <input type="text" value={thumbText} onChange={(e) => setThumbText(e.target.value)} placeholder="Thumbnail text" style={{ width: "100%", fontSize: 14, padding: "10px 13px" }} />
            <div className="set-actions" style={{ marginTop: 10 }}>
              <button className="btn btn-ghost btn-sm" onClick={useCurrentFrame} disabled={busy !== null}>Use current frame</button>
              <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()} disabled={busy !== null}>
                {busy === "upload" ? <div className="spinner sm" /> : "Upload image"}
              </button>
              <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.webp" style={{ display: "none" }}
                onChange={(e) => e.target.files?.[0] && uploadImage(e.target.files[0])} />
              <span style={{ fontSize: 12, color: "var(--text3)" }}>{source}</span>
            </div>
            <div style={{ marginTop: 12 }}>
              <button className="btn btn-primary btn-sm" onClick={applyThumbnail} disabled={busy !== null}>
                {busy === "thumb" ? <div className="spinner sm" /> : "Apply thumbnail"}
              </button>
            </div>
          </div>

          <div className="section">
            <label style={labelStyle}>Details</label>
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

          <div className="set-actions">
            <button className="btn btn-ghost btn-sm" onClick={reopen} disabled={busy !== null}>
              {busy === "reopen" ? <div className="spinner sm" /> : "Reopen in editor"}
            </button>
            {davinciOn && (
              <button className="btn btn-ghost btn-sm" onClick={exportDavinci} disabled={busy !== null}>
                {busy === "davinci" ? <div className="spinner sm" /> : "Export for DaVinci"}
              </button>
            )}
          </div>
          {msg && <div className="set-note ok" style={{ wordBreak: "break-all" }}>{msg}</div>}
        </div>

        <div className="preview-col">
          <div className="preview-panel">
            {previewFile ? (
              <ClipPlayer key={previewUrl} src={previewUrl} onTime={(t) => (playerTime.current = t)} />
            ) : (
              <div className="phone-empty">No rendered output</div>
            )}
            <label style={{ ...labelStyle, marginTop: 18 }}>Thumbnail</label>
            <div className="thumb-stage">
              {clip.thumbnail_config?.preview_path ? (
                <img key={`gen-${previewBust}`} src={`/api/stream-source?path=${encodeURIComponent(clip.thumbnail_config.preview_path)}&t=${previewBust}`} alt="thumbnail" />
              ) : thumbImage ? (
                <img src={`/api/stream-source?path=${encodeURIComponent(thumbImage)}`} alt="thumbnail source" />
              ) : (
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text3)", fontSize: 12, textAlign: "center", padding: 16 }}>
                  No thumbnail yet — set the text and a frame, then Apply.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
