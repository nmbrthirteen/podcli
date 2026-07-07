import React, { useEffect, useRef, useState } from "react";
import { api, fmtMs } from "./lib";
import { PlayIcon, PauseIcon, FrameBackIcon, FrameForwardIcon, CutBackIcon, CutForwardIcon, CloseIcon } from "./icons";

interface Keyframe { tAbs: number; x_pct: number }

const FRAME = 1 / 30;
const BUFFER = 7; // seconds of padding shown around the clip for trimming

export default function ReframeEditor({
  clipId, start, end, caption_style, onClose, onDone,
}: {
  clipId: string; start: number; end: number; caption_style: string;
  onClose: () => void; onDone: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const dragMode = useRef<"in" | "out" | "scrub" | null>(null);

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
  const [cuts, setCuts] = useState<number[]>([]);
  const [detectingCuts, setDetectingCuts] = useState(true);

  const bufStart = Math.max(0, start - BUFFER);
  const bufEnd = Math.min(srcDur, end + BUFFER);
  const win = Math.max(0.1, bufEnd - bufStart);
  const pos = (t: number) => ((t - bufStart) / win) * 100;
  const half = boxPct / 2;
  const clampCenter = (p: number) => Math.max(half, Math.min(100 - half, p));

  useEffect(() => {
    api(`/clips/${clipId}/reframe`).then((r) => {
      if (Array.isArray(r?.keyframes)) setKeyframes(r.keyframes);
      if (typeof r?.inSec === "number") setInSec(r.inSec);
      if (typeof r?.outSec === "number") setOutSec(r.outSec);
    }).catch(() => {});
    api(`/clips/${clipId}/cuts`)
      .then((r) => setCuts(Array.isArray(r?.cuts) ? r.cuts : []))
      .catch(() => {})
      .finally(() => setDetectingCuts(false));
  }, [clipId]);

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
    const near = cuts.find((c) => Math.abs(c - tAbs) < 0.25);
    const t = near ?? tAbs;
    setKeyframes((kf) => [...kf.filter((k) => Math.abs(k.tAbs - t) > 0.02), { tAbs: +t.toFixed(3), x_pct: +centerPct.toFixed(1) }].sort((a, b) => a.tAbs - b.tAbs));
  };

  const jumpCut = (dir: 1 | -1) => {
    const next = dir > 0 ? cuts.find((c) => c > tAbs + 0.05) : [...cuts].reverse().find((c) => c < tAbs - 0.05);
    if (next != null) seek(next);
  };

  const xToTime = (clientX: number) => {
    const r = trackRef.current!.getBoundingClientRect();
    return bufStart + Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * win;
  };
  const applyDrag = (clientX: number) => {
    const tt = xToTime(clientX);
    if (dragMode.current === "in") { setInSec(Math.min(tt, outSec - 0.2)); seek(tt); }
    else if (dragMode.current === "out") { setOutSec(Math.max(tt, inSec + 0.2)); seek(tt); }
    else seek(tt);
  };
  const onTrackDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const tt = xToTime(e.clientX);
    const thr = win * 0.05;
    dragMode.current = Math.abs(tt - inSec) < thr ? "in" : Math.abs(tt - outSec) < thr ? "out" : "scrub";
    applyDrag(e.clientX);
  };

  const apply = async () => {
    const kf = keyframes.length ? keyframes : [{ tAbs: inSec, x_pct: +centerPct.toFixed(1) }];
    setBusy(true); setErr(null);
    try {
      const r = await api(`/clips/${clipId}/rerender`, {
        method: "POST",
        body: JSON.stringify({ caption_style, reframe: { keyframes: kf, inSec: +inSec.toFixed(3), outSec: +outSec.toFixed(3) } }),
      });
      if (r.error) throw new Error(r.error);
      onDone();
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };

  return (
    <div className="reframe-overlay" onClick={onClose}>
      <div className="reframe-modal" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>Reframe & trim</h3>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close"><CloseIcon /></button>
        </div>

        <div ref={stageRef} className="reframe-stage" onPointerDown={pointer} onPointerMove={pointer}>
          <video ref={videoRef} src={`/api/clips/${clipId}/source`} playsInline preload="auto"
            onLoadedMetadata={onMeta} onTimeUpdate={onTimeUpdate} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
          <div className="reframe-box" style={{ left: `${centerPct - half}%`, width: `${boxPct}%` }} />
        </div>

        <div ref={trackRef} className="rf-track" onPointerDown={onTrackDown} onPointerMove={(e) => { if (e.buttons) applyDrag(e.clientX); }} onPointerUp={() => (dragMode.current = null)}>
          <div className="rf-region" style={{ left: `${pos(inSec)}%`, width: `${pos(outSec) - pos(inSec)}%` }} />
          <div className="rf-handle rf-handle-in" style={{ left: `${pos(inSec)}%` }} />
          <div className="rf-handle rf-handle-out" style={{ left: `${pos(outSec)}%` }} />
          {cuts.map((c) => <div key={`cut-${c}`} className="rf-cut" style={{ left: `${pos(c)}%` }} title={`camera switch @ ${fmtMs(c)}`} />)}
          {keyframes.map((k) => <div key={k.tAbs} className="rf-kf" style={{ left: `${pos(k.tAbs)}%` }} title={`${fmtMs(k.tAbs)} · ${Math.round(k.x_pct)}%`} />)}
          <div className="rf-playhead" style={{ left: `${pos(tAbs)}%` }} />
        </div>

        <div className="hint" style={{ marginTop: 6 }}>
          {detectingCuts ? "Detecting camera switches…" : cuts.length ? `${cuts.length} camera switch${cuts.length > 1 ? "es" : ""} detected. A keyframe near one snaps to it.` : "No camera switches detected"}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 8 }}>
          <button className="btn btn-ghost btn-sm" title="Play / pause" onClick={togglePlay}>{playing ? <PauseIcon /> : <PlayIcon />}</button>
          <button className="btn btn-ghost btn-sm" title="Previous frame" onClick={() => seek(tAbs - FRAME)}><FrameBackIcon /></button>
          <button className="btn btn-ghost btn-sm" title="Next frame" onClick={() => seek(tAbs + FRAME)}><FrameForwardIcon /></button>
          <button className="btn btn-ghost btn-sm" title="Previous camera switch" onClick={() => jumpCut(-1)} disabled={!cuts.length}><CutBackIcon /></button>
          <button className="btn btn-ghost btn-sm" title="Next camera switch" onClick={() => jumpCut(1)} disabled={!cuts.length}><CutForwardIcon /></button>
          <span style={{ fontSize: 12, color: "var(--text)", fontVariantNumeric: "tabular-nums", marginLeft: 4 }}>{fmtMs(tAbs)}</span>
          <span style={{ flex: 1 }} />
          <button className="btn btn-primary btn-sm" onClick={addKeyframe}>+ Keyframe</button>
        </div>

        {keyframes.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 12 }}>
            {keyframes.map((k) => (
              <span key={k.tAbs} className="pill" style={{ fontSize: 11, display: "inline-flex", gap: 6, alignItems: "center", cursor: "pointer" }} onClick={() => seek(k.tAbs)}>
                {fmtMs(k.tAbs)} · {Math.round(k.x_pct)}%
                <button className="asset-pill-x" onClick={(e) => { e.stopPropagation(); setKeyframes((kf) => kf.filter((x) => x.tAbs !== k.tAbs)); }}><CloseIcon /></button>
              </span>
            ))}
          </div>
        )}

        {err && <div className="set-note err" style={{ marginTop: 12 }}>{err}</div>}

        <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
          <button className="btn btn-primary btn-sm" onClick={apply} disabled={busy}>{busy ? <div className="spinner sm" /> : "Apply & re-render"}</button>
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
