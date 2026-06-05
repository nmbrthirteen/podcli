import React, { useRef, useState } from "react";
import { api } from "./lib";

interface Keyframe { tAbs: number; x_pct: number }

const FRAME = 1 / 30;
const BUFFER = 7; // seconds of padding shown around the clip for trimming
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
  const [srcDur, setSrcDur] = useState(end + BUFFER);
  const [tAbs, setTAbs] = useState(start);
  const [inSec, setInSec] = useState(start);
  const [outSec, setOutSec] = useState(end);
  const [keyframes, setKeyframes] = useState<Keyframe[]>([]);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const bufStart = Math.max(0, start - BUFFER);
  const bufEnd = Math.min(srcDur, end + BUFFER);
  const win = Math.max(0.1, bufEnd - bufStart);
  const pos = (t: number) => ((t - bufStart) / win) * 100;
  const half = boxPct / 2;
  const clampCenter = (p: number) => Math.max(half, Math.min(100 - half, p));

  const onMeta = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.videoWidth) setBoxPct(Math.min(100, ((v.videoHeight * 9) / 16 / v.videoWidth) * 100));
    if (v.duration && Number.isFinite(v.duration)) setSrcDur(v.duration);
    v.currentTime = start;
  };

  const reflectKeyframe = (absT: number) => {
    const at = keyframes.slice().sort((a, b) => a.tAbs - b.tAbs).filter((k) => k.tAbs <= absT).pop();
    if (at) setCenterPct(at.x_pct);
  };

  const seek = (absT: number) => {
    const v = videoRef.current;
    const clamped = Math.max(bufStart, Math.min(bufEnd, absT));
    if (v) v.currentTime = clamped;
    setTAbs(clamped);
    reflectKeyframe(clamped);
  };

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) { if (v.currentTime >= bufEnd) v.currentTime = bufStart; v.play(); } else v.pause();
  };

  const onTimeUpdate = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.currentTime >= bufEnd) { v.pause(); v.currentTime = bufEnd; }
    setTAbs(v.currentTime);
    reflectKeyframe(v.currentTime);
  };

  const pointer = (e: React.PointerEvent) => {
    if (e.buttons === 0 && e.type !== "pointerdown") return;
    const r = stageRef.current!.getBoundingClientRect();
    setCenterPct(clampCenter(((e.clientX - r.left) / r.width) * 100));
  };

  const addKeyframe = () => {
    setKeyframes((kf) => [...kf.filter((k) => Math.abs(k.tAbs - tAbs) > 0.02), { tAbs: +tAbs.toFixed(3), x_pct: +centerPct.toFixed(1) }].sort((a, b) => a.tAbs - b.tAbs));
  };

  const jumpKeyframe = (dir: 1 | -1) => {
    const kf = keyframes.slice().sort((a, b) => a.tAbs - b.tAbs);
    const next = dir > 0 ? kf.find((k) => k.tAbs > tAbs + 0.001) : [...kf].reverse().find((k) => k.tAbs < tAbs - 0.001);
    if (next) seek(next.tAbs);
  };

  const apply = async () => {
    const kf = keyframes
      .filter((k) => k.tAbs >= inSec - 0.001 && k.tAbs <= outSec + 0.001)
      .map((k) => ({ t: +Math.max(0, k.tAbs - inSec).toFixed(3), x_pct: k.x_pct }));
    const crop_keyframes = kf.length ? kf : [{ t: 0, x_pct: +centerPct.toFixed(1) }];
    setBusy(true); setErr(null);
    try {
      const r = await api(`/clips/${clipId}/rerender`, {
        method: "POST",
        body: JSON.stringify({ crop_keyframes, caption_style, start_second: +inSec.toFixed(3), end_second: +outSec.toFixed(3) }),
      });
      if (r.error) throw new Error(r.error);
      onDone();
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };

  const label: React.CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: "0.5px", textTransform: "uppercase", color: "var(--text2)", marginBottom: 8, display: "block" };

  return (
    <div className="reframe-overlay" onClick={onClose}>
      <div className="reframe-modal" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>Reframe & trim</h3>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>✕</button>
        </div>

        <div ref={stageRef} className="reframe-stage" onPointerDown={pointer} onPointerMove={pointer}>
          <video ref={videoRef} src={`/api/clips/${clipId}/source`} muted playsInline preload="auto"
            onLoadedMetadata={onMeta} onTimeUpdate={onTimeUpdate} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
          <div className="reframe-box" style={{ left: `${centerPct - half}%`, width: `${boxPct}%` }} />
        </div>

        <div className="rf-track" onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); seek(bufStart + ((e.clientX - r.left) / r.width) * win); }}>
          <div className="rf-region" style={{ left: `${pos(inSec)}%`, width: `${pos(outSec) - pos(inSec)}%` }} />
          <div className="rf-handle" style={{ left: `${pos(inSec)}%` }} title="Clip start" />
          <div className="rf-handle" style={{ left: `${pos(outSec)}%` }} title="Clip end" />
          {keyframes.map((k) => <div key={k.tAbs} className="rf-kf" style={{ left: `${pos(k.tAbs)}%` }} title={`${fmtMs(k.tAbs)} · ${Math.round(k.x_pct)}%`} />)}
          <div className="rf-playhead" style={{ left: `${pos(tAbs)}%` }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--text3)", marginTop: 4 }}>
          <span>{fmtMs(bufStart)}</span><span>buffer ±{BUFFER}s around clip</span><span>{fmtMs(bufEnd)}</span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
          <button className="btn btn-ghost btn-sm" title="Play/pause" onClick={togglePlay}>{playing ? "❚❚" : "▶"}</button>
          <button className="btn btn-ghost btn-sm" title="-1 frame" onClick={() => seek(tAbs - FRAME)}>◀</button>
          <button className="btn btn-ghost btn-sm" title="+1 frame" onClick={() => seek(tAbs + FRAME)}>▶</button>
          <button className="btn btn-ghost btn-sm" title="Previous keyframe" onClick={() => jumpKeyframe(-1)}>⏮</button>
          <button className="btn btn-ghost btn-sm" title="Next keyframe" onClick={() => jumpKeyframe(1)}>⏭</button>
          <span style={{ fontSize: 12, color: "var(--text)", fontVariantNumeric: "tabular-nums", marginLeft: 4 }}>{fmtMs(tAbs)}</span>
          <span style={{ flex: 1 }} />
          <button className="btn btn-ghost btn-sm" onClick={() => setInSec(Math.min(tAbs, outSec - 0.2))}>Set start</button>
          <button className="btn btn-ghost btn-sm" onClick={() => setOutSec(Math.max(tAbs, inSec + 0.2))}>Set end</button>
          <button className="btn btn-primary btn-sm" onClick={addKeyframe}>+ Keyframe</button>
        </div>

        <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 8 }}>
          Clip: {fmtMs(inSec)} → {fmtMs(outSec)} ({(outSec - inSec).toFixed(1)}s)
        </div>

        <div style={{ marginTop: 12 }}>
          <label style={label}>Keyframes</label>
          {keyframes.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--text3)" }}>Drag the window to frame the shot. Add keyframes to snap framing at cuts; Set start/end to trim (±{BUFFER}s buffer).</div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {keyframes.map((k) => (
                <span key={k.tAbs} className="pill" style={{ fontSize: 11, display: "inline-flex", gap: 6, alignItems: "center", cursor: "pointer" }} onClick={() => seek(k.tAbs)}>
                  {fmtMs(k.tAbs)} · {Math.round(k.x_pct)}%
                  <button onClick={(e) => { e.stopPropagation(); setKeyframes((kf) => kf.filter((x) => x.tAbs !== k.tAbs)); }} style={{ background: "none", border: "none", color: "var(--text3)", cursor: "pointer", padding: 0 }}>×</button>
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
