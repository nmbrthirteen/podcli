import React, { useEffect, useRef, useState } from "react";

const fmt = (s: number) =>
  Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "0:00";

export default function ClipPlayer({ src, onTime }: { src: string; onTime?: (t: number) => void }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);

  useEffect(() => {
    setPlaying(false);
    setT(0);
  }, [src]);

  const toggle = () => {
    const v = ref.current;
    if (!v) return;
    if (v.paused) v.play();
    else v.pause();
  };

  const seekAt = (clientX: number, el: HTMLDivElement) => {
    const v = ref.current;
    if (!v || !dur) return;
    const rect = el.getBoundingClientRect();
    v.currentTime = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) * dur;
  };

  const pct = dur ? (t / dur) * 100 : 0;

  return (
    <div className={`clip-player ${playing ? "" : "paused"}`}>
      <video
        ref={ref}
        src={src}
        playsInline
        onClick={toggle}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => { setT(e.currentTarget.currentTime); onTime?.(e.currentTarget.currentTime); }}
        onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
      />
      <div className="clip-player-bar">
        <button className="clip-player-btn" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
          {playing ? "❚❚" : "▶"}
        </button>
        <div
          className="clip-player-track"
          onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); seekAt(e.clientX, e.currentTarget); }}
          onPointerMove={(e) => { if (e.buttons) seekAt(e.clientX, e.currentTarget); }}
        >
          <div className="clip-player-fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="clip-player-time">{fmt(t)} / {fmt(dur)}</span>
      </div>
    </div>
  );
}
