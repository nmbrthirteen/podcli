import React, { useEffect, useRef, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { api, upload, fmt, basename, labelStyle } from "./lib";
import ClipPlayer from "./ClipPlayer";
import ReframeEditor from "./ReframeEditor";

interface ThumbnailConfig {
  text?: string;
  line1?: string;
  line2?: string;
  image_path?: string;
  timestamp?: number;
  preview_path?: string;
  variations?: string[];
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
const img = (p: string, bust: number) => `/api/image?path=${encodeURIComponent(p)}&t=${bust}`;

export default function ClipDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const playerTime = useRef(0);

  const [clip, setClip] = useState<Clip | null>(null);
  const [loading, setLoading] = useState(true);
  const [title, setTitle] = useState("");
  const [captionStyle, setCaptionStyle] = useState("");
  const [line1, setLine1] = useState("");
  const [line2, setLine2] = useState("");
  const [thumbImage, setThumbImage] = useState<string | null>(null);
  const [thumbTimestamp, setThumbTimestamp] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [bust, setBust] = useState(1);
  const [davinciOn, setDavinciOn] = useState(false);
  const [reframing, setReframing] = useState(false);

  const load = () => {
    api("/history?limit=500")
      .then((d: Clip[]) => {
        const found = Array.isArray(d) ? d.find((c) => c.id === id || String(c.id).startsWith(id || "\0")) : null;
        setClip(found || null);
        if (found) {
          setTitle(found.title);
          setCaptionStyle(found.caption_style);
          const tc = found.thumbnail_config || {};
          setLine1(tc.line1 ?? "");
          setLine2(tc.line2 ?? "");
          setThumbImage(tc.image_path ?? null);
          setThumbTimestamp(tc.image_path ? null : tc.timestamp ?? null);
        }
      })
      .finally(() => setLoading(false));
  };
  useEffect(load, [id]);
  useEffect(() => {
    api("/integrations")
      .then((d) => setDavinciOn(!!(d.integrations || []).find((i: any) => i.name === "davinci_resolve" && i.enabled)))
      .catch(() => {});
  }, []);

  if (loading) return <div className="app"><div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text2)", padding: 40 }}><div className="spinner sm" /> Loading…</div></div>;
  if (!clip) return <div className="app"><div className="header"><h1>Clip not found</h1></div><Link to="/" style={{ color: "var(--accent)" }}>← Library</Link></div>;

  const tc = clip.thumbnail_config || {};
  const dirty = title !== clip.title || captionStyle !== clip.caption_style;
  const previewUrl = `/api/clips/${clip.id}/preview?t=${bust}`;
  const source = thumbImage ? `Image · ${basename(thumbImage)}` : thumbTimestamp != null ? `Frame @ ${fmt(thumbTimestamp)}` : "Auto";

  const patch = (body: any) => api(`/clips/${clip.id}`, { method: "PATCH", body: JSON.stringify(body) });

  const save = async () => {
    setBusy("save"); setMsg(null);
    try {
      const r = await patch({ title, caption_style: captionStyle });
      if (r.error) throw new Error(r.error);
      load();
    } catch (e: any) { setMsg(`Save failed: ${e.message}`); } finally { setBusy(null); }
  };

  const useCurrentFrame = () => { setThumbTimestamp(clip.start_second + playerTime.current); setThumbImage(null); setMsg(null); };

  const uploadImage = async (f: File) => {
    setBusy("upload"); setMsg(null);
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await upload<any>("/upload", fd);
      if (!r.file_path) throw new Error("upload failed");
      setThumbImage(r.file_path); setThumbTimestamp(null);
    } catch (e: any) { setMsg(`Upload failed: ${e.message}`); } finally { setBusy(null); }
  };

  const generate = async () => {
    setBusy("thumb"); setMsg(null);
    try {
      const cfg: ThumbnailConfig = { line1: line1 || undefined, line2: line2 || undefined };
      if (thumbImage) cfg.image_path = thumbImage;
      else if (thumbTimestamp != null) cfg.timestamp = thumbTimestamp;
      const p = await patch({ thumbnail_config: cfg });
      if (p.error) throw new Error(p.error);
      const r = await api(`/clips/${clip.id}/thumbnail`, { method: "POST", body: "{}" });
      if (r.error) throw new Error(r.error);
      setBust(Date.now()); load();
    } catch (e: any) { setMsg(`Thumbnail failed: ${e.message}`); } finally { setBusy(null); }
  };

  const pickVariation = async (p: string) => {
    setBusy("pick");
    try {
      const r = await api(`/clips/${clip.id}/thumbnail/select`, { method: "POST", body: JSON.stringify({ path: p }) });
      if (r.error) throw new Error(r.error);
      setBust(Date.now()); load();
    } catch (e: any) { setMsg(`Pick failed: ${e.message}`); } finally { setBusy(null); }
  };

  const reopen = async () => {
    setBusy("reopen"); setMsg(null);
    try {
      const r = await api(`/clips/${clip.id}/reopen`, { method: "POST", body: "{}" });
      if (r.error) throw new Error(r.error);
      navigate("/episode");
    } catch (e: any) { setMsg(`Reopen failed: ${e.message}`); setBusy(null); }
  };

  const exportDavinci = async () => {
    setBusy("davinci"); setMsg(null);
    try {
      const r = await api(`/clips/${clip.id}/davinci`, { method: "POST", body: "{}" });
      if (r.error) throw new Error(r.error);
      setMsg(r.path ? `DaVinci project: ${r.path}` : "Exported");
    } catch (e: any) { setMsg(`DaVinci export failed: ${e.message}`); } finally { setBusy(null); }
  };

  const del = async () => {
    if (!window.confirm(`Delete "${clip.title}"? This removes the rendered file too.`)) return;
    setBusy("delete"); setMsg(null);
    try {
      await api(`/clips/${clip.id}`, { method: "DELETE" });
      navigate("/");
    } catch (e: any) { setMsg(`Delete failed: ${e.message}`); setBusy(null); }
  };

  return (
    <div className="app">
      <div className="header">
        <Link to="/" style={{ fontSize: 12, color: "var(--text3)", textDecoration: "none" }}>← Library</Link>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 16, marginTop: 8, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0 }}>{clip.title}</h1>
          <div className="set-actions">
            <a className="btn btn-ghost btn-sm" href={`/api/clips/${clip.id}/download`} download style={{ textDecoration: "none" }}>Download</a>
            <button className="btn btn-ghost btn-sm" onClick={reopen} disabled={busy !== null}>{busy === "reopen" ? <div className="spinner sm" /> : "Reopen in editor"}</button>
            {davinciOn && <button className="btn btn-ghost btn-sm" onClick={exportDavinci} disabled={busy !== null}>{busy === "davinci" ? <div className="spinner sm" /> : "Export for DaVinci"}</button>}
            <button className="btn btn-danger btn-sm" onClick={del} disabled={busy !== null}>{busy === "delete" ? <div className="spinner sm" /> : "Delete"}</button>
          </div>
        </div>
      </div>

      <div className="clip-detail">
        <div className="clip-detail-player">
          {file ? <ClipPlayer key={previewUrl} src={previewUrl} onTime={(t) => (playerTime.current = t)} /> : <div className="phone-empty">No rendered output</div>}
          <button className="btn btn-ghost btn-sm" style={{ width: "100%", marginTop: 10 }} onClick={() => setReframing(true)}>Reframe (fix camera)</button>
          <div className="clip-meta">
            <span>{fmt(clip.start_second)}–{fmt(clip.end_second)} · {clip.duration}s</span>
            <span>{clip.crop_strategy}</span>
            {clip.content_type && <span>{clip.content_type}</span>}
            {clip.file_size_mb != null && <span>{clip.file_size_mb.toFixed(1)}MB</span>}
          </div>
          <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 8 }}>{basename(clip.source_video)}</div>
        </div>

        <div className="clip-detail-editor">
          {msg && <div className="set-note ok" style={{ wordBreak: "break-all" }}>{msg}</div>}

          <div className="section">
            <label style={labelStyle}>Title & captions</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: "100%", fontSize: 14, padding: "10px 13px" }} />
            <div style={{ display: "flex", gap: 10, marginTop: 10, alignItems: "center" }}>
              <select value={captionStyle} onChange={(e) => setCaptionStyle(e.target.value)} style={{ flex: 1 }}>
                {CAPTION_STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              <button className="btn btn-primary btn-sm" onClick={save} disabled={!dirty || busy !== null}>
                {busy === "save" ? <div className="spinner sm" /> : "Save"}
              </button>
            </div>
          </div>

          <div className="section">
            <label style={labelStyle}>Thumbnail</label>
            <div className="thumb-edit">
              <div className="thumb-edit-preview">
                <div className="thumb-stage">
                  {tc.preview_path ? (
                    <img key={`gen-${bust}`} src={img(tc.preview_path, bust)} alt="thumbnail" />
                  ) : thumbImage ? (
                    <img src={img(thumbImage, bust)} alt="thumbnail source" />
                  ) : (
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text3)", fontSize: 11, textAlign: "center", padding: 12 }}>
                      Generate to preview
                    </div>
                  )}
                </div>
              </div>
              <div className="thumb-edit-controls">
                <input type="text" value={line1} onChange={(e) => setLine1(e.target.value)} placeholder="Line 1" style={{ width: "100%", fontSize: 14, padding: "9px 12px" }} />
                <input type="text" value={line2} onChange={(e) => setLine2(e.target.value)} placeholder="Line 2 (highlighted)" style={{ width: "100%", fontSize: 14, padding: "9px 12px", marginTop: 8 }} />
                <div className="set-actions" style={{ marginTop: 10 }}>
                  <button className="btn btn-ghost btn-sm" onClick={useCurrentFrame} disabled={busy !== null}>Use current frame</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()} disabled={busy !== null}>
                    {busy === "upload" ? <div className="spinner sm" /> : "Upload image"}
                  </button>
                  <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.webp" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && uploadImage(e.target.files[0])} />
                </div>
                <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 8 }}>Source · {source}</div>
                <div style={{ marginTop: 12 }}>
                  <button className="btn btn-primary btn-sm" onClick={generate} disabled={busy !== null}>
                    {busy === "thumb" ? <div className="spinner sm" /> : (tc.variations?.length ? "Regenerate" : "Generate thumbnail")}
                  </button>
                </div>
              </div>
            </div>
            {(tc.variations?.length ?? 0) > 0 && (
              <div className="thumb-variations" style={{ marginTop: 14 }}>
                {tc.variations!.map((v) => (
                  <button key={v} className={`thumb-variation ${tc.preview_path === v ? "selected" : ""}`} onClick={() => pickVariation(v)} disabled={busy !== null}>
                    <img src={img(v, bust)} alt="" />
                  </button>
                ))}
              </div>
            )}
          </div>

          {clip.transcript_slice && (
            <div className="section">
              <label style={labelStyle}>Transcript</label>
              <div style={{ fontSize: 13, color: "var(--text2)", lineHeight: 1.7 }}>{clip.transcript_slice}</div>
            </div>
          )}
        </div>
      </div>

      {reframing && (
        <ReframeEditor
          clipId={clip.id}
          start={clip.start_second}
          end={clip.end_second}
          caption_style={clip.caption_style}
          onClose={() => setReframing(false)}
          onDone={() => { setReframing(false); setBust(Date.now()); setMsg("Reframed & re-rendered"); load(); }}
        />
      )}
    </div>
  );
}
