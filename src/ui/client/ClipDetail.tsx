import React, { useEffect, useRef, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { api, upload, fmt, basename, labelStyle } from "./lib";
import ClipPlayer from "./ClipPlayer";
import { BackIcon } from "./icons";
import ReframeEditor from "./ReframeEditor";
import CopyButton from "./CopyButton";

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
  generated_titles?: string[];
  description?: string;
  tags?: string;
  hashtags?: string;
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
  const [textOpts, setTextOpts] = useState<[string, string][]>([]);
  const [frameOpts, setFrameOpts] = useState<any[]>([]);
  const [frameIdx, setFrameIdx] = useState(0);
  const [selFrame, setSelFrame] = useState<{ path: string; info?: any } | null>(null);
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
  if (!clip) return <div className="app"><div className="header"><h1>Clip not found</h1></div><Link to="/" style={{ color: "var(--accent)", display: "inline-flex", alignItems: "center", gap: 4 }}><BackIcon /> Library</Link></div>;

  const tc = clip.thumbnail_config || {};
  const dirty = title !== clip.title || captionStyle !== clip.caption_style;
  const previewUrl = `/api/clips/${clip.id}/preview?t=${bust}`;

  const patch = (body: any) => api(`/clips/${clip.id}`, { method: "PATCH", body: JSON.stringify(body) });

  const save = async () => {
    setBusy("save"); setMsg(null);
    try {
      const r = await patch({ title, caption_style: captionStyle });
      if (r.error) throw new Error(r.error);
      load();
    } catch (e: any) { setMsg(`Save failed: ${e.message}`); } finally { setBusy(null); }
  };

  const loadOptions = async () => {
    setBusy("options"); setMsg(null);
    try {
      const r = await api(`/clips/${clip.id}/thumbnail/options?texts=6&frames=6`);
      if (r.error) throw new Error(r.error);
      setTextOpts(r.texts || []);
      setFrameOpts(r.frames || []);
      if ((r.frames || []).length) setSelFrame({ path: r.frames[0].path, info: r.frames[0] });
      if (!(r.texts || []).length && !(r.frames || []).length) setMsg("No options. Is the AI CLI installed and the source video available?");
    } catch (e: any) { setMsg(`Options failed: ${e.message}`); } finally { setBusy(null); }
  };

  const uploadFrame = async (f: File) => {
    setBusy("upload"); setMsg(null);
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await upload<any>("/upload", fd);
      if (!r.file_path) throw new Error("upload failed");
      setSelFrame({ path: r.file_path });
    } catch (e: any) { setMsg(`Upload failed: ${e.message}`); } finally { setBusy(null); }
  };

  const renderThumb = async () => {
    if (!selFrame) { setMsg("Select or upload a frame first"); return; }
    setBusy("render"); setMsg(null);
    try {
      const r = await api(`/clips/${clip.id}/thumbnail/render`, {
        method: "POST",
        body: JSON.stringify({ line1: line1 || undefined, line2: line2 || undefined, frame_path: selFrame.path, frame_info: selFrame.info }),
      });
      if (r.error) throw new Error(r.error);
      setBust(Date.now()); load();
      setMsg("Thumbnail generated");
    } catch (e: any) { setMsg(`Generate failed: ${e.message}`); } finally { setBusy(null); }
  };

  // Pull a different frame from the clip (cycles the ranked candidates) and
  // re-render — same one-click flow as Regenerate, but swaps the background frame.
  const newFrame = async () => {
    setBusy("newframe"); setMsg(null);
    try {
      let frames = frameOpts;
      if (!frames.length) {
        const r = await api(`/clips/${clip.id}/thumbnail/options?texts=6&frames=8`);
        if (r.error) throw new Error(r.error);
        frames = r.frames || [];
        setFrameOpts(frames);
        if ((r.texts || []).length) setTextOpts(r.texts);
      }
      if (!frames.length) { setMsg("No frames found in this clip"); return; }
      const curIdx = frames.findIndex((f: any) => f.path === selFrame?.path);
      const next = ((curIdx < 0 ? frameIdx : curIdx) + 1) % frames.length;
      setFrameIdx(next);
      const frame = frames[next];
      setSelFrame({ path: frame.path, info: frame });
      const r = await api(`/clips/${clip.id}/thumbnail/render`, {
        method: "POST",
        body: JSON.stringify({ line1: line1 || undefined, line2: line2 || undefined, frame_path: frame.path, frame_info: frame }),
      });
      if (r.error) throw new Error(r.error);
      setBust(Date.now()); load();
      setMsg(`New frame from clip (${next + 1}/${frames.length})`);
    } catch (e: any) { setMsg(`New frame failed: ${e.message}`); } finally { setBusy(null); }
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

  const generateContent = async () => {
    setBusy("content"); setMsg(null);
    try {
      const body = {
        clip: {
          id: clip.id, title: clip.title,
          start_second: clip.start_second, end_second: clip.end_second,
          content_type: clip.content_type,
        },
        transcript_segments: clip.transcript_slice
          ? [{ start: clip.start_second, text: clip.transcript_slice }]
          : [],
      };
      const r = await api("/generate-content", { method: "POST", body: JSON.stringify(body) });
      if (r.error) throw new Error(r.error);
      if (!r.titles?.length && !r.description) throw new Error("AI CLI returned nothing. Is claude/codex installed?");
      load();
    } catch (e: any) { setMsg(`Content generation failed: ${e.message}`); } finally { setBusy(null); }
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
        <Link to="/" style={{ fontSize: 12, color: "var(--text3)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}><BackIcon size={12} /> Library</Link>
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
          {clip.output_path ? <ClipPlayer key={previewUrl} src={previewUrl} onTime={(t) => (playerTime.current = t)} /> : <div className="phone-empty">No rendered output</div>}
          <button className="btn btn-ghost btn-sm" style={{ width: "100%", marginTop: 10 }} onClick={() => setReframing(true)}>Reframe (fix camera)</button>
          <div className="clip-meta">
            <span>{fmt(clip.start_second)}-{fmt(clip.end_second)} · {clip.duration}s</span>
            <span>{clip.crop_strategy}</span>
            {clip.content_type && <span>{clip.content_type}</span>}
            {clip.file_size_mb != null && <span>{clip.file_size_mb.toFixed(1)}MB</span>}
          </div>
          <div className="hint" style={{ marginTop: 8 }}>{basename(clip.source_video)}</div>
        </div>

        <div className="clip-detail-editor">
          {msg && <div className="set-note ok" style={{ wordBreak: "break-all" }}>{msg}</div>}

          <div className="section">
            <label style={labelStyle}>Title & captions</label>
            <textarea value={title} onChange={(e) => setTitle(e.target.value)} rows={2} style={{ width: "100%", resize: "vertical", lineHeight: 1.5 }} />
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
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <label style={{ ...labelStyle, marginBottom: 0 }}>Thumbnail</label>
              <button className="btn btn-ghost btn-sm" onClick={loadOptions} disabled={busy !== null}>
                {busy === "options" ? <><div className="spinner sm" /> Finding options…</> : (textOpts.length || frameOpts.length ? "Refresh options" : "Get options")}
              </button>
            </div>

            <div className="thumb-edit">
              <div className="thumb-edit-preview">
                <div className="thumb-stage">
                  {busy === "render" ? (
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 8, color: "var(--text2)", fontSize: 11 }}>
                      <div className="spinner sm" /> Rendering…
                    </div>
                  ) : tc.preview_path ? (
                    <img key={`gen-${bust}`} src={img(tc.preview_path, bust)} alt="thumbnail" />
                  ) : selFrame ? (
                    <img src={img(selFrame.path, bust)} alt="selected frame" />
                  ) : (
                    <div className="hint" style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", textAlign: "center", padding: 12 }}>
                      Get options, pick a frame, then generate
                    </div>
                  )}
                </div>
              </div>
              <div className="thumb-edit-controls">
                <input type="text" value={line1} onChange={(e) => setLine1(e.target.value)} placeholder="Line 1" style={{ width: "100%" }} />
                <input type="text" value={line2} onChange={(e) => setLine2(e.target.value)} placeholder="Line 2 (highlighted)" style={{ width: "100%", marginTop: 8 }} />
                <div className="set-actions" style={{ marginTop: 10 }}>
                  <button className="btn btn-primary btn-sm" onClick={renderThumb} disabled={busy !== null || !selFrame}>
                    {busy === "render" ? <div className="spinner sm" /> : (tc.preview_path ? "Regenerate" : "Generate")}
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={newFrame} disabled={busy !== null} title="Pull a different frame from the clip">
                    {busy === "newframe" ? <div className="spinner sm" /> : "New frame"}
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()} disabled={busy !== null}>
                    {busy === "upload" ? <div className="spinner sm" /> : "Upload frame"}
                  </button>
                  <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.webp" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && uploadFrame(e.target.files[0])} />
                </div>
                <div className="hint" style={{ marginTop: 8 }}>Leave line 1 and line 2 empty to auto-write the text.</div>
              </div>
            </div>

            {textOpts.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <div className="hint" style={{ marginBottom: 6 }}>Text options · click to use</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  {textOpts.map(([l1, l2], i) => (
                    <button key={i} className={`title-option ${line1 === l1 && line2 === l2 ? "selected" : ""}`} onClick={() => { setLine1(l1); setLine2(l2); }}>
                      <strong>{l1}</strong>{l2 ? ` · ${l2}` : ""}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {frameOpts.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <div className="hint" style={{ marginBottom: 6 }}>Frame options · click to select</div>
                <div className="thumb-variations">
                  {frameOpts.map((f, i) => (
                    <button key={i} className={`thumb-variation ${selFrame?.path === f.path ? "selected" : ""}`} onClick={() => setSelFrame({ path: f.path, info: f })} disabled={busy !== null}>
                      <img src={img(f.path, bust)} alt="" />
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="section">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <label style={{ ...labelStyle, marginBottom: 0 }}>Titles & description</label>
              <button className="btn btn-ghost btn-sm" onClick={generateContent} disabled={busy !== null}>
                {busy === "content" ? <div className="spinner sm" /> : (clip.generated_titles?.length || clip.description ? "Regenerate" : "Generate")}
              </button>
            </div>
            {!clip.generated_titles?.length && !clip.description ? (
              <div style={{ fontSize: 12, color: "var(--text3)" }}>Generate titles, a description, tags, and hashtags for this clip.</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {clip.generated_titles?.length ? (
                  <div>
                    <div className="hint" style={{ marginBottom: 6 }}>Title options · click to use</div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                      {clip.generated_titles.map((t, i) => {
                        const clean = t.replace(/^\d+\.\s*/, "");
                        return (
                          <button key={i} className={`title-option ${title === clean ? "selected" : ""}`} onClick={() => { setTitle(clean); setMsg("Title set. Click save to apply"); }}>
                            {t}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
                {clip.description ? (
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                      <span className="hint">Description</span>
                      <CopyButton text={clip.description} />
                    </div>
                    <div style={{ fontSize: 13, color: "var(--text2)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{clip.description}</div>
                  </div>
                ) : null}
                {clip.tags ? (
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                      <span className="hint">Tags</span>
                      <CopyButton text={clip.tags} />
                    </div>
                    <div className="meta" style={{ lineHeight: 1.6 }}>{clip.tags}</div>
                  </div>
                ) : null}
                {clip.hashtags ? (
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                      <span className="hint">Hashtags</span>
                      <CopyButton text={clip.hashtags} />
                    </div>
                    <div style={{ fontSize: 12, color: "var(--accent)", lineHeight: 1.6 }}>{clip.hashtags}</div>
                  </div>
                ) : null}
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
