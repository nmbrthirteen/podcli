import React, { useEffect, useRef, useState } from "react";
import { api } from "./lib";

interface Keyframe { t: number; x_pct: number }

const FRAME = 1 / 30; // ~33ms nudge for catching a cut
const fmtMs = (s: number) =>
  `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}.${String(Math.floor((s % 1) * 1000)).padStart(3, "0")}`;

export default function ReframeEditor({
  clipId, start, end, caption_style, onClose, onDone,
}: {
  clipId: string; start: number; end: number; caption_style: string;
  onClose: () => void; onDone: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [boxPct, setBoxPct] = useState(31.6);
  const [centerPct, setCenterPct] = useState(50);
  const [t, setT] = useState(0); // clip-relative seconds
  const [keyframes, setKeyframes] = useState<Keyframe[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const dur = Math.max(0.1, end - start);

  const onMeta = () => {
    const v = videoRef.current;
    if (v && v.videoWidth) setBoxPct(Math.min(100, ((v.videoHeight * 9) / 16 / v.videoWidth) * 100));
    if (v) v.currentTime = start;
  };

  const seek = (clipT: number) => {
    const v = videoRef.current;
    if (v) v.currentTime = start + Math.max(0, Math.min(dur, clipT));
    setT(clipT);
    // Reflect the keyframe framing at this time, if any.
    const kf = keyframes.slice().sort((a, b) => a.t - b.t);
    const at = kf.filter((k) => k.t <= clipT).pop() || kf[0];
    if (at) setCenterPct(at.x_pct);
  };

  const half = boxPct / 2;
  const clampCenter = (p: number) => Math.max(half, Math.min(100 - half, p));

  const pointer = (e: React.PointerEvent) => {
    if (e.buttons === 0 && e.type !== "pointerdown") return;
    const rect = stageRef.current!.getBoundingClientRect();
    setCenterPct(clampCenter(((e.clientX - rect.left) / rect.width) * 100));
  };

  const addKeyframe = () => {
    setKeyframes((kf) => [...kf.filter((k) => Math.abs(k.t - t) > 0.02), { t: +t.toFixed(3), x_pct: +centerPct.toFixed(1) }].sort((a, b) => a.t - b.t));
  };

  const apply = async () => {
    const kf = keyframes.length ? keyframes : [{ t: 0, x_pct: +centerPct.toFixed(1) }];
    setBusy(true); setErr(null);
    try {
      const r = await api(`/clips/${clipId}/rerender`, { method: "POST", body: JSON.stringify({ crop_keyframes: kf, caption_style }) });
      if (r.error) throw new Error(r.error);
      onDone();
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      if (v.currentTime < start || v.currentTime >= start + dur) v.currentTime = start;
      v.play(); setPlaying(true);
    } else { v.pause(); setPlaying(false); }
  };

  const onTimeUpdate = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.currentTime >= start + dur) { v.pause(); setPlaying(false); v.currentTime = start + dur; }
    const clipT = Math.max(0, Math.min(dur, v.currentTime - start));
    setT(clipT);
    const kf = keyframes.slice().sort((a, b) => a.t - b.t);
    const at = kf.filter((k) => k.t <= clipT).pop();
    if (at) setCenterPct(at.x_pct);
  };

  const jumpKeyframe = (dir: 1 | -1) => {
    const kf = keyframes.slice().sort((a, b) => a.t - b.t);
    const next = dir > 0 ? kf.find((k) => k.t > t + 0.001) : [...kf].reverse().find((k) => k.t < t - 0.001);
    if (next) seek(next.t);
  };

  const label: React.CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: "0.5px", textTransform: "uppercase", color: "var(--text2)", marginBottom: 8, display: "block" };

  return (
    <div className="reframe-overlay" onClick={onClose}>
      <div className="reframe-modal" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>Reframe</h3>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>✕</button>
        </div>

        <div ref={stageRef} className="reframe-stage" onPointerDown={pointer} onPointerMove={pointer}>
          <video ref={videoRef} src={`/api/clips/${clipId}/source`} muted playsInline preload="auto" onLoadedMetadata={onMeta} onTimeUpdate={onTimeUpdate} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
          <div className="reframe-box" style={{ left: `${centerPct - half}%`, width: `${boxPct}%` }} />
        </div>

        <div
          className="rf-track"
          onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); seek(((e.clientX - r.left) / r.width) * dur); }}
        >
          {keyframes.map((k) => <div key={k.t} className="rf-kf" style={{ left: `${(k.t / dur) * 100}%` }} title={`${fmtMs(k.t)} · ${Math.round(k.x_pct)}%`} />)}
          <div className="rf-playhead" style={{ left: `${(t / dur) * 100}%` }} />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 8 }}>
          <button className="btn btn-ghost btn-sm" title="Play/pause" onClick={togglePlay}>{playing ? "❚❚" : "▶"}</button>
          <button className="btn btn-ghost btn-sm" title="-1 frame" onClick={() => seek(t - FRAME)}>◀</button>
          <button className="btn btn-ghost btn-sm" title="+1 frame" onClick={() => seek(t + FRAME)}>▶</button>
          <button className="btn btn-ghost btn-sm" title="Previous keyframe" onClick={() => jumpKeyframe(-1)}>⏮</button>
          <button className="btn btn-ghost btn-sm" title="Next keyframe" onClick={() => jumpKeyframe(1)}>⏭</button>
          <span style={{ fontSize: 12, color: "var(--text)", fontVariantNumeric: "tabular-nums", marginLeft: 6 }}>{fmtMs(t)} / {fmtMs(dur)}</span>
          <span style={{ flex: 1 }} />
          <button className="btn btn-primary btn-sm" onClick={addKeyframe}>+ Keyframe here</button>
        </div>

        <div style={{ marginTop: 12 }}>
          <label style={label}>Keyframes</label>
          {keyframes.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--text3)" }}>Drag the window to frame the shot. Add keyframes to pan over time, or just set one position.</div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {keyframes.map((k) => (
                <span key={k.t} className="pill" style={{ fontSize: 11, display: "inline-flex", gap: 6, alignItems: "center", cursor: "pointer" }} onClick={() => seek(k.t)}>
                  {fmtMs(k.t)} · {Math.round(k.x_pct)}%
                  <button onClick={(e) => { e.stopPropagation(); setKeyframes((kf) => kf.filter((x) => x.t !== k.t)); }} style={{ background: "none", border: "none", color: "var(--text3)", cursor: "pointer", padding: 0 }}>×</button>
                </span>
              ))}
            </div>
          )}
        </div>

        {err && <div className="set-note err" style={{ marginTop: 12 }}>{err}</div>}

        <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
          <button className="btn btn-primary btn-sm" onClick={apply} disabled={busy}>{busy ? <div className="spinner sm" /> : "Apply & re-render"}</button>
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
