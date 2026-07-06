import React, { useEffect, useRef, useState } from "react";
import { api, basename } from "./lib";
import { BackIcon, TrashIcon, PlayIcon, PauseIcon, DownloadIcon } from "./icons";

interface Moment {
  start: number;
  end: number;
  why: string;
  text: string;
  source?: string;
  enabled: boolean;
  dirty: boolean;
  clip_path?: string;
  clip_exists?: boolean;
}

interface HighlightsResp {
  session_id: string;
  source: string;
  sources?: string[];
  format: string;
  logo?: string;
  out_dir: string;
  reel_path: string | null;
  moments: Moment[];
}

interface SessionSummary {
  session_id: string;
  source: string;
  profile: string;
  format: string;
  moment_count: number;
  enabled_count: number;
  source_count?: number;
  reel_path: string | null;
}

type Format = "vertical" | "horizontal" | "square";

const download = (p: string) => `/api/reel-download?path=${encodeURIComponent(p)}`;
const isHttpUrl = (v: string) => /^https?:\/\//i.test(v.trim());

const FORMAT_LABEL: Record<Format, string> = {
  horizontal: "16:9",
  vertical: "9:16",
  square: "1:1",
};

const mmss = (s: number) => {
  const t = Math.max(0, s);
  return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="field-label">{label}</label>
      {children}
    </div>
  );
}

function MomentTrim({
  src,
  moment,
  onCommit,
  saving,
}: {
  src: string;
  moment: Moment;
  onCommit: (start: number, end: number) => void;
  saving: boolean;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const [dur, setDur] = useState(0);
  const [cur, setCur] = useState(moment.start);
  const [playing, setPlaying] = useState(false);
  const [draft, setDraft] = useState<{ start: number; end: number } | null>(null);
  const drag = useRef<null | "in" | "out">(null);

  const start = draft?.start ?? moment.start;
  const end = draft?.end ?? moment.end;

  const pad = Math.max(8, (moment.end - moment.start) * 0.6);
  const winA = Math.max(0, moment.start - pad);
  const winB = Math.min(dur || moment.end + pad, moment.end + pad);
  const span = Math.max(0.1, winB - winA);
  const pct = (t: number) => `${((t - winA) / span) * 100}%`;

  useEffect(() => {
    const v = video.current;
    if (v) v.currentTime = moment.start;
    setCur(moment.start);
  }, [moment.start, moment.end, src]);

  const timeAt = (clientX: number) => {
    const el = trackRef.current;
    if (!el) return winA;
    const r = el.getBoundingClientRect();
    return winA + Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * span;
  };

  const onMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const t = timeAt(e.clientX);
    setDraft((d) => {
      const base = d ?? { start: moment.start, end: moment.end };
      if (drag.current === "in") return { start: Math.min(t, base.end - 0.5), end: base.end };
      return { start: base.start, end: Math.max(t, base.start + 0.5) };
    });
    if (video.current) {
      video.current.currentTime = t;
      setCur(t);
    }
  };

  const endDrag = (e: React.PointerEvent) => {
    if (!drag.current) return;
    e.currentTarget.releasePointerCapture(e.pointerId);
    drag.current = null;
    if (draft) onCommit(round1(draft.start), round1(draft.end));
  };

  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) {
      if (v.currentTime < start || v.currentTime >= end) v.currentTime = start;
      v.play();
    } else v.pause();
  };

  const onTime = (e: React.SyntheticEvent<HTMLVideoElement>) => {
    const t = e.currentTarget.currentTime;
    if (playing && t >= end) {
      e.currentTarget.currentTime = start;
      setCur(start);
    } else setCur(t);
  };

  return (
    <div>
      <div className="clip-player" style={{ marginBottom: 10 }}>
        <video
          ref={video}
          src={src}
          playsInline
          preload="metadata"
          onClick={toggle}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onTimeUpdate={onTime}
          onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
          style={{ maxHeight: 360, width: "100%", objectFit: "contain", background: "#000" }}
        />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button className="clip-player-btn" onClick={toggle} aria-label={playing ? "Pause" : "Play"}
          style={{ flexShrink: 0 }}>
          {playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <div
          ref={trackRef}
          className="rf-track"
          style={{ flex: 1, marginTop: 0 }}
          onPointerMove={onMove}
          onPointerUp={endDrag}
        >
          <div className="rf-region" style={{ left: pct(start), width: `calc(${pct(end)} - ${pct(start)})` }} />
          <div className="rf-playhead" style={{ left: pct(cur) }} />
          <div
            className="rf-handle"
            style={{ left: pct(start), background: "var(--accent)" }}
            onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); drag.current = "in"; }}
          />
          <div
            className="rf-handle"
            style={{ left: pct(end), background: "var(--accent)" }}
            onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); drag.current = "out"; }}
          />
        </div>
        <span className="clip-player-time" style={{ minWidth: 118, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
          {saving ? "saving…" : `${mmss(start)}–${mmss(end)} · ${Math.round(end - start)}s`}
        </span>
      </div>
    </div>
  );
}

const round1 = (n: number) => Math.round(n * 10) / 10;

interface JobState {
  status?: string;
  progress?: number;
  message?: string;
  error?: string;
  result?: { file_path?: string };
}

function useJob(jobId: string | null): JobState | null {
  const [state, setState] = useState<JobState | null>(null);
  useEffect(() => {
    if (!jobId) {
      setState(null);
      return;
    }
    const es = new EventSource(`/api/job/${jobId}/stream`);
    es.onmessage = (e) => {
      const d = JSON.parse(e.data) as JobState;
      setState(d);
      if (d.status === "done" || d.status === "error") es.close();
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [jobId]);
  return state;
}

function DownloadRow({
  jobId,
  url,
  onDone,
  onError,
}: {
  jobId: string;
  url: string;
  onDone: (jobId: string, filePath?: string) => void;
  onError: (jobId: string, error?: string) => void;
}) {
  const job = useJob(jobId);
  const fired = useRef(false);
  useEffect(() => {
    if (!job || fired.current) return;
    if (job.status === "done") {
      fired.current = true;
      onDone(jobId, job.result?.file_path);
    } else if (job.status === "error") {
      fired.current = true;
      onError(jobId, job.error);
    }
  }, [job?.status]);
  return (
    <div className="file-badge">
      <div className="spinner sm" />
      <div className="name">
        Downloading {basename(url)}
        {job?.progress ? ` · ${Math.round(job.progress)}%` : ""}
      </div>
    </div>
  );
}

export default function HighlightsPage() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [videoPaths, setVideoPaths] = useState<string[]>([]);
  const [downloads, setDownloads] = useState<{ jobId: string; url: string }[]>([]);
  const [pathDraft, setPathDraft] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [format, setFormat] = useState<Format>("horizontal");
  const [auto, setAuto] = useState(true);
  const [topN, setTopN] = useState(10);
  const [minDur, setMinDur] = useState(15);
  const [maxDur, setMaxDur] = useState(60);
  const [logoPath, setLogoPath] = useState("");
  const [logoUploading, setLogoUploading] = useState(false);
  const createLogoRef = useRef<HTMLInputElement>(null);
  const sessionLogoRef = useRef<HTMLInputElement>(null);
  const [session, setSession] = useState<HighlightsResp | null>(null);
  const [selected, setSelected] = useState(0);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function refreshList() {
    try {
      const r = await api<{ sessions: SessionSummary[] }>("/reel", {
        method: "POST",
        body: JSON.stringify({ action: "list" }),
      });
      setSessions(r.sessions || []);
    } catch {
      /* list is best-effort */
    }
  }

  useEffect(() => {
    refreshList();
    api<{ settings?: { logoPath?: string } }>("/ui-state")
      .then((s) => { if (s.settings?.logoPath) setLogoPath(s.settings.logoPath); })
      .catch(() => {});
  }, []);

  async function call(body: Record<string, unknown>, label: string) {
    setBusy(true);
    setMsg(label);
    try {
      const r = await api<HighlightsResp>("/reel", { method: "POST", body: JSON.stringify(body) });
      setSession(r);
      setSelected(0);
      setMsg(null);
      refreshList();
    } catch (e: unknown) {
      setMsg("Error: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  }

  const addPath = (p: string) =>
    setVideoPaths((prev) => (p && !prev.includes(p) ? [...prev, p] : prev));
  const removePath = (i: number) => setVideoPaths((prev) => prev.filter((_, n) => n !== i));

  async function browse() {
    setBrowsing(true);
    try {
      const d = await api<{ file_path?: string; file_paths?: string[] }>("/browse-file?multiple=1");
      const picked = d.file_paths?.length ? d.file_paths : d.file_path ? [d.file_path] : [];
      setVideoPaths((prev) => [...prev, ...picked.filter((p) => !prev.includes(p))]);
    } catch {
      /* dialog cancelled */
    } finally {
      setBrowsing(false);
    }
  }

  async function uploadDropped(files: File[]) {
    setBrowsing(true);
    try {
      for (const file of files) {
        setMsg(`Uploading ${file.name}…`);
        const fd = new FormData();
        fd.append("file", file);
        const res = await fetch("/api/upload", { method: "POST", body: fd });
        const d = (await res.json()) as { file_path?: string; error?: string };
        if (d.file_path) addPath(d.file_path);
        else setMsg(d.error || "Upload failed");
      }
    } catch {
      setMsg("Upload failed");
    } finally {
      setBrowsing(false);
      setMsg(null);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length) uploadDropped(files);
  }

  async function startDownload(url: string) {
    try {
      const d = await api<{ job_id?: string; error?: string }>("/download-video", {
        method: "POST",
        body: JSON.stringify({ url }),
      });
      if (d.error) setMsg("Download failed: " + d.error);
      else if (d.job_id) setDownloads((prev) => [...prev, { jobId: d.job_id!, url }]);
    } catch (e) {
      setMsg("Download failed: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  const onDownloadDone = (jobId: string, filePath?: string) => {
    setDownloads((prev) => prev.filter((d) => d.jobId !== jobId));
    if (filePath) addPath(filePath);
    else setMsg("Download finished without a video file.");
  };
  const onDownloadError = (jobId: string, error?: string) => {
    setDownloads((prev) => prev.filter((d) => d.jobId !== jobId));
    setMsg("Download failed: " + (error || "unknown error"));
  };

  function commitDraft() {
    const p = pathDraft.trim();
    if (p) {
      if (isHttpUrl(p)) startDownload(p);
      else addPath(p);
    }
    setPathDraft("");
  }

  async function uploadImage(f: File): Promise<string | null> {
    setLogoUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const d = (await res.json()) as { file_path?: string; error?: string };
      if (d.file_path) {
        api("/ui-state", {
          method: "POST",
          body: JSON.stringify({ _source: "ui", settings: { logoPath: d.file_path } }),
        }).catch(() => {});
        return d.file_path;
      }
      setMsg(d.error || "Logo upload failed");
      return null;
    } catch {
      setMsg("Logo upload failed");
      return null;
    } finally {
      setLogoUploading(false);
    }
  }

  async function applySessionLogo(f: File | null) {
    if (!session) return;
    const logo = f ? await uploadImage(f) : "";
    if (f && !logo) return;
    call(
      { action: "build", session_id: session.session_id, logo },
      f ? "Adding logo and rebuilding…" : "Removing logo…",
    );
  }

  const detect = () => {
    const seed =
      videoPaths.length === 1 ? { video_path: videoPaths[0] } : { video_paths: videoPaths };
    const tuning = auto ? { auto: true } : { top_n: topN, min_dur: minDur, max_dur: maxDur };
    call(
      { action: "new", ...seed, profile: "auto", format, ...tuning, ...(logoPath ? { logo: logoPath } : {}) },
      videoPaths.length > 1
        ? `Finding the best moments across ${videoPaths.length} videos (one-time)…`
        : "Finding the best moments (one-time, ~2 min)…",
    );
  };

  const open = (id: string) => call({ action: "show", session_id: id }, "Loading…");

  const editOp = (index: number, op: string, seconds = 0) =>
    session &&
    call({ action: "edit", session_id: session.session_id, index, op, seconds }, "Rebuilding…");

  async function commitTrim(index: number, start: number, end: number) {
    if (!session) return;
    setSession({ ...session, moments: session.moments.map((m, i) => (i === index ? { ...m, start, end } : m)) });
    setSaving(true);
    try {
      const r = await api<HighlightsResp>("/reel", {
        method: "POST",
        body: JSON.stringify({ action: "edit", session_id: session.session_id, index: index + 1, op: "set", start, end }),
      });
      setSession(r);
    } finally {
      setSaving(false);
    }
  }

  async function remove(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    if (!window.confirm("Delete this highlights session?")) return;
    setBusy(true);
    try {
      await api("/reel", { method: "POST", body: JSON.stringify({ action: "delete", session_id: id }) });
      if (session?.session_id === id) setSession(null);
      refreshList();
    } finally {
      setBusy(false);
    }
  }

  const enabled = session?.moments.filter((m) => m.enabled).length ?? 0;
  const active = session?.moments[selected];
  const activeSource = active?.source || session?.source || "";
  const streamSrc = activeSource ? `/api/stream-source?path=${encodeURIComponent(activeSource)}` : "";

  return (
    <div className="app">
      <div className="header">
        <h1 style={{ margin: 0 }}>Highlights</h1>
      </div>

      <div className="section card">
        <div className="card-title" style={{ marginBottom: 14 }}>Find highlights</div>

        <div
          className={`drop-zone ${dragOver ? "drag-over" : ""}`}
          style={{ cursor: browsing ? "default" : "pointer" }}
          onClick={browsing ? undefined : browse}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          {browsing ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10 }}>
              <div className="spinner sm" /> <span style={{ fontSize: 13, fontWeight: 600 }}>Working…</span>
            </div>
          ) : (
            <div className="label"><strong>Browse</strong> or drop video files here</div>
          )}
        </div>

        {(videoPaths.length > 0 || downloads.length > 0) && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
            {videoPaths.map((p, i) => (
              <div key={p} className="file-badge fade-in">
                <div className="dot" />
                <div className="name">{basename(p)}</div>
                <button className="btn btn-ghost btn-sm" onClick={() => removePath(i)} style={{ padding: "4px 10px", fontSize: 11 }}>
                  Remove
                </button>
              </div>
            ))}
            {downloads.map((d) => (
              <DownloadRow key={d.jobId} jobId={d.jobId} url={d.url} onDone={onDownloadDone} onError={onDownloadError} />
            ))}
          </div>
        )}
        <input
          type="text"
          placeholder="…or paste a local path or YouTube/video URL, press Enter to add"
          value={pathDraft}
          onChange={(e) => setPathDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitDraft(); } }}
          onBlur={commitDraft}
          style={{ width: "100%", marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 12 }}
        />

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 16 }}>
          <span className="field-label" style={{ margin: 0 }}>Moments</span>
          <div style={{ display: "inline-flex", gap: 4 }}>
            <button className={`btn btn-sm ${auto ? "btn-primary" : "btn-ghost"}`} onClick={() => setAuto(true)}>Auto</button>
            <button className={`btn btn-sm ${!auto ? "btn-primary" : "btn-ghost"}`} onClick={() => setAuto(false)}>Custom</button>
          </div>
          {auto && <span className="hint">Best moments and how many, picked for you</span>}
        </div>

        <div className="row" style={{ marginTop: 14 }}>
          <Field label="Format">
            <select value={format} onChange={(e) => setFormat(e.target.value as Format)}>
              <option value="horizontal">Horizontal 16:9</option>
              <option value="vertical">Vertical 9:16</option>
              <option value="square">Square 1:1</option>
            </select>
          </Field>
          {!auto && (
            <>
              <Field label="Moments">
                <input type="number" min={1} max={50} value={topN} onChange={(e) => setTopN(Number(e.target.value))} style={{ width: "100%" }} />
              </Field>
              <Field label="Min length (s)">
                <input type="number" min={1} value={minDur} onChange={(e) => setMinDur(Number(e.target.value))} style={{ width: "100%" }} />
              </Field>
              <Field label="Max length (s)">
                <input type="number" min={1} value={maxDur} onChange={(e) => setMaxDur(Number(e.target.value))} style={{ width: "100%" }} />
              </Field>
            </>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14 }}>
          <span className="field-label" style={{ margin: 0 }}>Logo</span>
          {logoPath ? (
            <div className="file-badge">
              <div className="dot" />
              <div className="name">{basename(logoPath)}</div>
              <button className="btn btn-ghost btn-sm" onClick={() => setLogoPath("")} style={{ padding: "4px 10px", fontSize: 11 }}>Remove</button>
            </div>
          ) : (
            <button className="btn btn-ghost btn-sm" disabled={logoUploading} onClick={() => createLogoRef.current?.click()}>
              {logoUploading ? "Uploading…" : "Add logo"}
            </button>
          )}
          <span className="hint">Overlaid top-right on every clip</span>
          <input ref={createLogoRef} type="file" accept=".png,.jpg,.jpeg,.svg,.webp" style={{ display: "none" }}
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) uploadImage(f).then((p) => p && setLogoPath(p)); }} />
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
          <button className="btn btn-primary" disabled={busy || videoPaths.length === 0} onClick={detect}>
            {busy && msg?.startsWith("Finding")
              ? "Finding…"
              : videoPaths.length > 1
                ? `Find best across ${videoPaths.length} videos`
                : "Find highlights"}
          </button>
        </div>
      </div>

      {msg && (
        <div className="set-note" style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          {busy && <div className="spinner sm" />} {msg}
        </div>
      )}

      {session ? (
        <div className="stream-in">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
            <button className="btn btn-ghost btn-sm" onClick={() => setSession(null)} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <BackIcon /> All highlights
            </button>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span className="hint">{enabled}/{session.moments.length} in the cut</span>
              <button className="btn btn-ghost btn-sm" disabled={busy || logoUploading} onClick={() => sessionLogoRef.current?.click()}>
                {logoUploading ? "Uploading…" : session.logo ? "Change logo" : "Add logo"}
              </button>
              {session.logo && (
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => applySessionLogo(null)}>Remove logo</button>
              )}
              {session.reel_path && (
                <a className="btn btn-primary btn-sm" href={download(session.reel_path)} style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <DownloadIcon /> Download reel
                </a>
              )}
              <input ref={sessionLogoRef} type="file" accept=".png,.jpg,.jpeg,.svg,.webp" style={{ display: "none" }}
                onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) applySessionLogo(f); }} />
            </div>
          </div>

          {active && (
            <div className="section card" style={{ padding: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <span className="section-label" style={{ margin: 0 }}>Moment {selected + 1}</span>
                <span className="pill pill-blue">{active.why}</span>
                {(session.sources?.length ?? 1) > 1 && active.source && (
                  <span className="hint" title={active.source}>{basename(active.source)}</span>
                )}
                <span className="spacer" style={{ flex: 1 }} />
                {active.clip_exists && active.clip_path && (
                  <a className="btn btn-ghost btn-sm" href={download(active.clip_path)} style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <DownloadIcon /> Clip
                  </a>
                )}
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => editOp(selected + 1, "toggle")}>
                  {active.enabled ? "Exclude from cut" : "Include in cut"}
                </button>
                <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => editOp(selected + 1, "drop")}>
                  <TrashIcon />
                </button>
              </div>
              <MomentTrim
                key={selected}
                src={streamSrc}
                moment={active}
                saving={saving}
                onCommit={(s, e) => commitTrim(selected, s, e)}
              />
              {active.text && <p className="card-desc" style={{ marginTop: 12, marginBottom: 0 }}>{active.text}</p>}
            </div>
          )}

          <div className="section-label" style={{ margin: "4px 0 10px" }}>All moments</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {session.moments.map((m, idx) => (
              <div
                key={idx}
                className="card"
                onClick={() => setSelected(idx)}
                style={{
                  display: "flex", alignItems: "center", gap: 12, padding: "10px 14px", margin: 0, cursor: "pointer",
                  opacity: m.enabled ? 1 : 0.45,
                  borderColor: idx === selected ? "var(--accent-edge)" : "var(--border)",
                }}
              >
                <span className="hint" style={{ width: 20, textAlign: "right" }}>{idx + 1}</span>
                <strong style={{ fontVariantNumeric: "tabular-nums", minWidth: 96 }}>{mmss(m.start)}–{mmss(m.end)}</strong>
                <span className="pill pill-blue">{m.why}</span>
                {(session.sources?.length ?? 1) > 1 && m.source && (
                  <span className="hint" title={m.source} style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {basename(m.source)}
                  </span>
                )}
                <span className="hint" style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums" }}>{Math.round(m.end - m.start)}s</span>
                {m.clip_exists && m.clip_path && (
                  <a className="btn btn-ghost btn-sm" href={download(m.clip_path)} title="Download clip"
                    onClick={(e) => e.stopPropagation()} style={{ textDecoration: "none", padding: "4px 8px" }}>
                    <DownloadIcon size={13} />
                  </a>
                )}
              </div>
            ))}
          </div>
        </div>
      ) : sessions.length === 0 ? (
        <div className="empty-state">No highlights yet. Drop in one or more videos above to find their best moments.</div>
      ) : (
        <>
          <div className="section-label" style={{ marginBottom: 12 }}>Saved</div>
          <div className="stream-in">
            {sessions.map((s) => (
              <div
                key={s.session_id}
                className="card"
                style={{ display: "flex", alignItems: "center", gap: 12, padding: 16, cursor: "pointer" }}
                onClick={() => !busy && open(s.session_id)}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="clip-card-title" style={{ marginBottom: 4 }}>
                    {(s.source_count ?? 1) > 1
                      ? `${s.source_count} videos`
                      : basename(s.source) || s.session_id}
                  </div>
                  <div className="meta" style={{ gap: 8 }}>
                    <span className="pill pill-blue">{s.profile}</span>
                    <span className="hint">{FORMAT_LABEL[s.format as Format] || s.format}</span>
                    <span className="hint">·</span>
                    <span className="hint">{s.enabled_count}/{s.moment_count} moments</span>
                  </div>
                </div>
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={(e) => { e.stopPropagation(); open(s.session_id); }}>
                  Open
                </button>
                <button className="btn btn-danger btn-sm" title="Delete" disabled={busy} onClick={(e) => remove(e, s.session_id)}>
                  <TrashIcon />
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
