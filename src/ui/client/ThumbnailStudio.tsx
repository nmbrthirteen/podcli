import React, { useRef, useState } from "react";
import { api, upload, basename, labelStyle } from "./lib";
import ThumbnailTemplate from "./ThumbnailTemplate";

const img = (p: string, bust: number) => `/api/image?path=${encodeURIComponent(p)}&t=${bust}`;
const isImage = (name: string) => /\.(png|jpe?g|webp)$/i.test(name);

export default function ThumbnailStudio() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [video, setVideo] = useState<{ path: string; name: string } | null>(null);
  const [startS, setStartS] = useState("");
  const [endS, setEndS] = useState("");
  const [line1, setLine1] = useState("");
  const [line2, setLine2] = useState("");
  const [textOpts, setTextOpts] = useState<[string, string][]>([]);
  const [frameOpts, setFrameOpts] = useState<any[]>([]);
  const [selFrame, setSelFrame] = useState<{ path: string; info?: any } | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [bust, setBust] = useState(1);
  const [editingTemplate, setEditingTemplate] = useState(false);

  if (editingTemplate) {
    return <ThumbnailTemplate onBack={() => { setEditingTemplate(false); setBust(Date.now()); }} />;
  }

  const browse = async () => {
    setBusy("browse"); setMsg(null);
    try {
      const r = await api("/browse-file");
      if (r.file_path) setVideo({ path: r.file_path, name: r.filename || basename(r.file_path) });
    } catch (e: any) {
      if (e.message !== "cancelled") setMsg(`Browse failed: ${e.message}`);
    } finally { setBusy(null); }
  };

  const uploadFile = async (f: File) => {
    setBusy("upload"); setMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await upload<any>("/upload", fd);
      if (!r.file_path) throw new Error("upload failed");
      if (isImage(f.name)) {
        setSelFrame({ path: r.file_path });
        setPreview(null);
        setMsg("Image set as the thumbnail background");
      } else {
        setVideo({ path: r.file_path, name: f.name });
      }
    } catch (e: any) { setMsg(`Upload failed: ${e.message}`); } finally { setBusy(null); }
  };

  const loadOptions = async () => {
    if (!title.trim()) { setMsg("Enter a title first, headlines are written from it"); return; }
    setBusy("options"); setMsg(null);
    try {
      const r = await api("/thumbnail-studio/options", {
        method: "POST",
        body: JSON.stringify({
          title,
          video_path: video?.path,
          start: startS !== "" ? Number(startS) : undefined,
          end: endS !== "" ? Number(endS) : undefined,
        }),
      });
      setTextOpts(r.texts || []);
      setFrameOpts(r.frames || []);
      setBust(Date.now());
      if ((r.frames || []).length) setSelFrame({ path: r.frames[0].path, info: r.frames[0] });
      if (!(r.texts || []).length && !(r.frames || []).length) setMsg("No options. Is the AI CLI installed? Pick a video for frame options.");
    } catch (e: any) { setMsg(`Options failed: ${e.message}`); } finally { setBusy(null); }
  };

  const render = async () => {
    if (!selFrame) { setMsg("Select a frame or upload an image first"); return; }
    setBusy("render"); setMsg(null);
    try {
      const r = await api("/thumbnail-studio/render", {
        method: "POST",
        body: JSON.stringify({
          title,
          line1: line1 || undefined,
          line2: line2 || undefined,
          frame_path: selFrame.path,
          frame_info: selFrame.info,
        }),
      });
      if (!r.path) throw new Error("no thumbnail produced");
      setPreview(r.path);
      setBust(Date.now());
      setMsg("Thumbnail generated");
    } catch (e: any) { setMsg(`Generate failed: ${e.message}`); } finally { setBusy(null); }
  };

  return (
    <div className="app">
      <div className="header">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0 }}>Thumbnail studio</h1>
          <button className="btn btn-ghost btn-sm" onClick={() => setEditingTemplate(true)}>Edit template</button>
        </div>
      </div>

      <div className="section">
        <label style={labelStyle}>Source</label>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn btn-ghost btn-sm" onClick={browse} disabled={busy !== null}>
            {busy === "browse" ? <div className="spinner sm" /> : "Browse video…"}
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()} disabled={busy !== null}>
            {busy === "upload" ? <div className="spinner sm" /> : "Upload video or image"}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".mp4,.mov,.mkv,.webm,.png,.jpg,.jpeg"
            style={{ display: "none" }}
            onChange={(e) => e.target.files?.[0] && uploadFile(e.target.files[0])}
          />
          {video && <span style={{ fontSize: 12, color: "var(--text2)" }}>{video.name}</span>}
        </div>
        <div style={{ display: "flex", gap: 10, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title to write headlines from" style={{ flex: "1 1 280px", fontSize: 14, padding: "10px 13px" }} />
          <input type="number" min={0} value={startS} onChange={(e) => setStartS(e.target.value)} placeholder="Start (s)" style={{ width: 90, fontSize: 13, padding: "9px 10px" }} />
          <input type="number" min={0} value={endS} onChange={(e) => setEndS(e.target.value)} placeholder="End (s)" style={{ width: 90, fontSize: 13, padding: "9px 10px" }} />
          <button className="btn btn-primary btn-sm" onClick={loadOptions} disabled={busy !== null}>
            {busy === "options" ? <><div className="spinner sm" /> Finding options…</> : (textOpts.length || frameOpts.length ? "Refresh options" : "Get options")}
          </button>
        </div>
      </div>

      {msg && <div className="set-note ok" style={{ wordBreak: "break-word" }}>{msg}</div>}

      <div className="section">
        <label style={labelStyle}>Thumbnail</label>
        <div className="thumb-edit">
          <div className="thumb-edit-preview">
            <div className="thumb-stage">
              {busy === "render" ? (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 8, color: "var(--text2)", fontSize: 11 }}>
                  <div className="spinner sm" /> Rendering…
                </div>
              ) : preview ? (
                <img key={`gen-${bust}`} src={img(preview, bust)} alt="thumbnail" />
              ) : selFrame ? (
                <img src={img(selFrame.path, bust)} alt="selected frame" />
              ) : (
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text3)", fontSize: 11, textAlign: "center", padding: 12 }}>
                  Pick a source and get options, or upload an image
                </div>
              )}
            </div>
          </div>
          <div className="thumb-edit-controls">
            <input type="text" value={line1} onChange={(e) => setLine1(e.target.value)} placeholder="Line 1" style={{ width: "100%", fontSize: 14, padding: "9px 12px" }} />
            <input type="text" value={line2} onChange={(e) => setLine2(e.target.value)} placeholder="Line 2 (highlighted)" style={{ width: "100%", fontSize: 14, padding: "9px 12px", marginTop: 8 }} />
            <div className="set-actions" style={{ marginTop: 10 }}>
              <button className="btn btn-primary btn-sm" onClick={render} disabled={busy !== null || !selFrame}>
                {busy === "render" ? <div className="spinner sm" /> : (preview ? "Regenerate" : "Generate")}
              </button>
              {preview && (
                <a className="btn btn-ghost btn-sm" href={img(preview, bust)} download="thumbnail.png" style={{ textDecoration: "none" }}>
                  Download
                </a>
              )}
            </div>
            <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 8 }}>Leave line 1 and line 2 empty to auto-write the text.</div>
          </div>
        </div>

        {textOpts.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontSize: 11, color: "var(--text3)", marginBottom: 6 }}>Text options · click to use</div>
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
            <div style={{ fontSize: 11, color: "var(--text3)", marginBottom: 6 }}>Frame options · click to select</div>
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
    </div>
  );
}
