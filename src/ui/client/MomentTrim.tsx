import React, { useEffect, useRef, useState } from "react";
import { fmt } from "./lib";
import { PlayIcon, PauseIcon } from "./icons";

const round1 = (n: number) => Math.round(n * 10) / 10;

export default function MomentTrim({
  src,
  start: startProp,
  end: endProp,
  onCommit,
  saving = false,
  onDuration,
}: {
  src: string;
  start: number;
  end: number;
  onCommit: (start: number, end: number) => void;
  saving?: boolean;
  onDuration?: (duration: number) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const [dur, setDur] = useState(0);
  const [cur, setCur] = useState(startProp);
  const [playing, setPlaying] = useState(false);
  const [draft, setDraft] = useState<{ start: number; end: number } | null>(null);
  const drag = useRef<null | "in" | "out">(null);

  const start = draft?.start ?? startProp;
  const end = draft?.end ?? endProp;

  const pad = Math.max(8, (endProp - startProp) * 0.6);
  const winA = Math.max(0, startProp - pad);
  const winB = Math.min(dur || endProp + pad, endProp + pad);
  const span = Math.max(0.1, winB - winA);
  const pct = (t: number) => `${((t - winA) / span) * 100}%`;

  useEffect(() => {
    setDraft(null);
    const v = video.current;
    if (v) v.currentTime = startProp;
    setCur(startProp);
  }, [startProp, endProp, src]);

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
      const base = d ?? { start: startProp, end: endProp };
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
          onLoadedMetadata={(e) => {
            setDur(e.currentTarget.duration);
            onDuration?.(e.currentTarget.duration);
          }}
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
          {saving ? "saving…" : `${fmt(start)}-${fmt(end)} · ${Math.round(end - start)}s`}
        </span>
      </div>
    </div>
  );
}
